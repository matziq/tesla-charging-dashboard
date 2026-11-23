import json
import sqlite3
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from dairy_charging import Dashboard, Ledger, ReviewWindow, read_candidate_pages, read_candidates, summarize, validate


def record(**changes):
    values = {
        "vehicle": "Example vehicle", "dairy": "Example dairy", "direction": "To dairy",
        "date": "11/23/2026", "time": "09:15", "location": "Example charger",
        "odometer": "12345", "trip_miles": "", "energy": "24.5", "rate": "",
        "paid": "",
    }
    values.update(changes)
    return values


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local" / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.addCleanup(lambda: self.ledger.close())

    def test_persistence_edit_and_unknown_amounts(self):
        self.assertEqual(self.ledger.rows(), [])
        self.ledger.save(record())
        first = self.ledger.rows()[0]
        self.assertIsNone(first["paid"])
        self.assertIsNone(first["rate"])
        self.assertIsNone(first["trip_miles"])
        self.ledger.save(record(paid="0", rate="0.250", trip_miles="42"), first["id"])
        self.ledger.close()
        self.ledger = Ledger(self.path)
        rows = self.ledger.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["paid"], "0")
        self.assertEqual(rows[0]["rate"], "0.250")
        self.assertEqual(rows[0]["trip_miles"], "42")

    def test_filters_and_summaries_do_not_sum_odometer_or_infer_payment(self):
        self.ledger.save(record(odometer="10000", trip_miles="10", energy="10.1", rate="1", paid=""))
        self.ledger.save(record(odometer="20000", trip_miles="", energy="2.2", rate="1", paid="20",
                                direction="From dairy", date="11/24/2026"))
        self.ledger.save(record(vehicle="Other vehicle", dairy="Other dairy", energy="5", paid="3"))
        rows = self.ledger.rows(vehicle="Example vehicle")
        totals = summarize(rows)
        self.assertEqual(str(totals["energy"]), "12.3")
        self.assertEqual(str(totals["paid"]), "20")
        self.assertEqual(totals["paid_missing"], 1)
        self.assertEqual(str(totals["trip"]), "10")
        self.assertEqual(totals["trip_missing"], 1)
        self.assertEqual(len(self.ledger.rows(dairy="Example dairy", direction="From dairy")), 1)
        self.assertEqual(len(self.ledger.rows(start="11/24/2026", end="11/24/2026")), 1)
        self.assertEqual(len(self.ledger.rows(vehicle="Other vehicle")), 1)
        self.assertEqual(self.ledger.options("vehicle"), ["Example vehicle", "Other vehicle"])

    def test_rejects_invalid_records_before_writing(self):
        for changes in (
            {"vehicle": ""}, {"dairy": ""}, {"location": "bad\nvalue"},
            {"direction": "Travel"}, {"date": "02/30/2026"}, {"date": "2026-11-23"},
            {"time": "9:15"}, {"time": "24:00"}, {"energy": ""},
            {"energy": "0"}, {"energy": "NaN"}, {"energy": "-1"},
            {"paid": "-1"}, {"rate": "Infinity"}, {"trip_miles": "0.0000001"},
        ):
            with self.subTest(field=next(iter(changes))), self.assertRaises(ValueError):
                self.ledger.save(record(**changes))
        self.assertEqual(self.ledger.rows(), [])

    def test_date_range_validation_and_stale_edit(self):
        with self.assertRaises(ValueError):
            self.ledger.rows(start="11/24/2026", end="11/23/2026")
        with self.assertRaises(ValueError):
            self.ledger.save(record(), 999)
        self.assertEqual(self.ledger.rows(), [])

    def test_actual_paid_not_calculated_from_rate(self):
        values = validate(record(energy="10", rate="0.5", paid="7.25"))
        self.assertEqual(values["energy"], "10")
        self.assertEqual(values["rate"], "0.5")
        self.assertEqual(values["paid"], "7.25")

    def test_import_requires_confirmation_and_prevents_duplicates_across_reopen(self):
        self.ledger.save(record(vehicle="Prior manual record"))
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.ledger.save_reviewed(record(), 101, False)
        self.assertEqual(len(self.ledger.rows()), 1)
        self.ledger.save_reviewed(record(energy="10", rate="", paid="", odometer="", trip_miles=""),
                                  101, True)
        self.ledger.close()
        self.ledger = Ledger(self.path)
        with self.assertRaisesRegex(ValueError, "already imported"):
            self.ledger.save_reviewed(record(), 101, True)
        self.assertEqual(len(self.ledger.rows()), 2)
        imported = next(row for row in self.ledger.rows() if row["vehicle"] == "Example vehicle")
        for field in ("rate", "paid", "odometer", "trip_miles"):
            self.assertIsNone(imported[field])
        self.ledger.save(record(paid="4.25"), imported["id"])
        self.assertTrue(self.ledger.imported(101))

    def test_import_rolls_back_record_when_identifier_save_fails(self):
        self.ledger.db.execute("""CREATE TRIGGER reject_review BEFORE INSERT ON reviewed_tesla_sessions
                               BEGIN SELECT RAISE(FAIL, 'synthetic detail'); END""")
        with self.assertRaises(ValueError) as failure:
            self.ledger.save_reviewed(record(), 101, True)
        self.assertNotIn("synthetic detail", str(failure.exception))
        self.assertEqual(self.ledger.rows(), [])
        self.assertFalse(self.ledger.imported(101))

    def test_import_rejects_bad_review_values_without_creating_identifier(self):
        with self.assertRaises(ValueError):
            self.ledger.save_reviewed(record(energy=""), 101, True)
        self.assertFalse(self.ledger.imported(101))
        self.assertEqual(self.ledger.rows(), [])

    def test_existing_ledger_gains_import_table_without_changing_records(self):
        self.ledger.save(record())
        self.ledger.db.execute("DROP TABLE reviewed_tesla_sessions")
        self.ledger.db.execute("DROP TABLE tesla_vehicle_associations")
        self.ledger.db.commit()
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(len(self.ledger.rows()), 1)
        self.assertIsNone(self.ledger.associated_vehicle("SYNTHETIC-ID"))
        self.ledger.save_reviewed(record(vehicle="Another vehicle"), 202, True)
        self.assertEqual(len(self.ledger.rows()), 2)

    def test_explicit_associations_persist_are_exact_and_can_be_corrected_or_forgotten(self):
        self.ledger.save(record(vehicle="Existing charge"))
        self.ledger.associate_vehicle("SYNTHETIC-ID-A", "Example car A")
        self.ledger.associate_vehicle("synthetic-id-a", "Example car B")
        self.ledger.close()
        self.ledger = Ledger(self.path)
        self.assertEqual(self.ledger.associated_vehicle("SYNTHETIC-ID-A"), "Example car A")
        self.assertEqual(self.ledger.associated_vehicle("synthetic-id-a"), "Example car B")
        self.ledger.associate_vehicle("SYNTHETIC-ID-A", "Corrected car")
        self.assertEqual(self.ledger.associated_vehicle("SYNTHETIC-ID-A"), "Corrected car")
        self.ledger.forget_vehicle_association("SYNTHETIC-ID-A")
        self.assertIsNone(self.ledger.associated_vehicle("SYNTHETIC-ID-A"))
        self.assertEqual(self.ledger.associated_vehicle("synthetic-id-a"), "Example car B")
        self.assertEqual(self.ledger.rows()[0]["vehicle"], "Existing charge")
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            self.ledger.forget_vehicle_association("SYNTHETIC-ID-A")

    def test_association_failures_roll_back_without_changing_records(self):
        self.ledger.associate_vehicle("SYNTHETIC-ID", "Original")
        self.ledger.save_reviewed(record(), 101, True)
        self.ledger.db.execute("""CREATE TRIGGER reject_association
                               BEFORE UPDATE ON tesla_vehicle_associations
                               BEGIN SELECT RAISE(FAIL, 'synthetic detail'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.associate_vehicle("SYNTHETIC-ID", "Replacement")
        self.assertEqual(self.ledger.associated_vehicle("SYNTHETIC-ID"), "Original")
        self.assertEqual(len(self.ledger.rows()), 1)
        with self.assertRaisesRegex(ValueError, "already imported"):
            self.ledger.save_reviewed(record(), 101, True)
        self.assertEqual(self.ledger.associated_vehicle("SYNTHETIC-ID"), "Original")

    def test_invalid_associations_are_rejected_without_disclosing_identifiers(self):
        for source in (None, "", " ", "SYNTHETIC\nSECRET", "x" * 501, "\ud800"):
            with self.subTest(case=type(source)), self.assertRaises(ValueError) as failure:
                self.ledger.associate_vehicle(source, "Example")
            self.assertNotIn("SYNTHETIC", str(failure.exception))
        for name in ("", "bad\nname", "\ud800"):
            with self.assertRaises(ValueError):
                self.ledger.associate_vehicle("SYNTHETIC-ID", name)
        self.assertIsNone(self.ledger.associated_vehicle(None))
        self.assertIsNone(self.ledger.associated_vehicle("SYNTHETIC-ID"))


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "response.bin"

    def test_reads_unverified_fee_clues_without_mapping_ledger_values(self):
        self.path.write_text(json.dumps({
            "data": [{"sessionId": 101, "vin": "SYNTHETIC",
                      "siteLocationName": "Sample site",
                      "chargeStartDateTime": "2026-01-01T10:00:00Z",
                      "chargeStopDateTime": "2026-01-01T10:30:00Z",
                      "fees": [{"totalDue": 42, "uom": "unknown"}]}],
            "totalResults": 2,
        }), encoding="utf-8")
        rows = read_candidates(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["siteLocationName"], "Sample site")
        self.assertEqual(rows[0]["source_vehicle_identifier"], "SYNTHETIC")
        self.assertNotIn("fees", rows[0])
        self.assertNotIn("energy", rows[0])
        self.assertIn('"totalDue": 42', rows[0]["fee_details"])
        self.assertIn('"uom": "unknown"', rows[0]["fee_details"])

    def test_missing_vehicle_identifier_cannot_create_a_false_association(self):
        for value in (None, "", " "):
            self.path.write_text(json.dumps({"data": [{"sessionId": 1, "vin": value}],
                                             "totalResults": 1}))
            self.assertIsNone(read_candidates(self.path)[0]["source_vehicle_identifier"])
        self.path.write_text(json.dumps({"data": [{"sessionId": 1, "vin": "Not provided"}],
                                         "totalResults": 1}))
        self.assertEqual(read_candidates(self.path)[0]["source_vehicle_identifier"], "Not provided")

    def test_preserves_each_fee_and_tier_with_exact_decimal_precision(self):
        body = ('{"data":[{"sessionId":1,"fees":['
                '{"feeType":"SYNTHETIC-A","rateBase":0.1234567890123456789,'
                '"usageTier1":2,"usageTier5":3,"totalDue":9,"isPaid":false},'
                '{"feeType":"SYNTHETIC-B","rateTier2":0.4,"totalTier2":1}]}],'
                '"totalResults":1}')
        self.path.write_text(body, encoding="utf-8")
        candidate = read_candidates(self.path)[0]
        self.assertEqual(json.loads(candidate["fee_details"]), json.loads(body)["data"][0]["fees"])
        for expected in ('0.1234567890123456789', '"SYNTHETIC-A"', '"SYNTHETIC-B"',
                         '"usageTier1": 2', '"usageTier5": 3', '"rateTier2": 0.4',
                         '"totalTier2": 1', '"isPaid": false'):
            self.assertIn(expected, candidate["fee_details"])
        self.assertNotIn("paid", candidate)
        self.assertNotIn("rate", candidate)

    def test_missing_null_empty_and_invalid_fees_remain_distinct(self):
        for fee, expected in ((None, "null"), ([], "[]")):
            self.path.write_text(json.dumps({"data": [{"sessionId": 1, "fees": fee}], "totalResults": 1}))
            self.assertEqual(read_candidates(self.path)[0]["fee_details"], expected)
        self.path.write_text('{"data":[{"sessionId":1}],"totalResults":1}')
        self.assertEqual(read_candidates(self.path)[0]["fee_details"], "Fee details not provided.")
        for fee in ("SYNTHETIC-PRIVATE", ["SYNTHETIC-PRIVATE"], [{"amount": float("nan")}],
                    [{"note": "SYNTHETIC-PRIVATE" * 10000}]):
            self.path.write_text(json.dumps({"data": [{"sessionId": 1, "fees": fee}], "totalResults": 1}))
            with self.assertRaises(ValueError) as failure:
                read_candidates(self.path)
            self.assertNotIn("SYNTHETIC-PRIVATE", str(failure.exception))
            self.assertNotIn(str(self.path), str(failure.exception))

    def test_rejects_invalid_page_without_disclosing_values(self):
        cases = (
            b'{"data":[{"sessionId":1},{"sessionId":1}],"totalResults":2}',
            b'{"data":[{"sessionId":true}],"totalResults":1}',
            b'{"data":[{"sessionId":1}],"totalResults":0}',
            b'{"data":[{"sessionId":1,"vin":"secret\\nvalue"}],"totalResults":1}',
            b'{"data":[],"totalResults":0,"totalResults":1}',
            b'{"data":"secret","totalResults":1}',
            b'{"data": ["secret"',
            b'{"data":[{"sessionId":1,"fees":[{"amount":1e999999999999999999999999}]}],"totalResults":1}',
        )
        for body in cases:
            with self.subTest(case=cases.index(body)):
                self.path.write_bytes(body)
                with self.assertRaises(ValueError) as failure:
                    read_candidates(self.path)
                self.assertNotIn("secret", str(failure.exception))
                self.assertNotIn(str(self.path), str(failure.exception))

    def test_rejects_oversized_and_missing_file(self):
        self.path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            read_candidates(self.path)
        with self.assertRaises(ValueError) as failure:
            read_candidates(self.path / "private-name")
        self.assertNotIn("private-name", str(failure.exception))

    def test_multiple_pages_are_reviewed_together_without_inference(self):
        second = self.path.with_name("second.bin")
        self.path.write_text(json.dumps({"data": [{"sessionId": 101}], "totalResults": 3}))
        second.write_text(json.dumps({"data": [{"sessionId": 202}], "totalResults": 3}))
        rows = read_candidate_pages([self.path, second])
        self.assertEqual([row["sessionId"] for row in rows], [101, 202])
        self.assertTrue(all("energy" not in row and "fees" not in row for row in rows))

    def test_overlapping_pages_and_invalid_later_page_are_rejected(self):
        second = self.path.with_name("second.bin")
        self.path.write_text(json.dumps({"data": [{"sessionId": 101}], "totalResults": 2}))
        for body in ('{"data":[{"sessionId":101}],"totalResults":2}', 'private invalid content'):
            second.write_text(body)
            with self.assertRaises(ValueError) as failure:
                read_candidate_pages([self.path, second])
            self.assertNotIn("101", str(failure.exception))
            self.assertNotIn("private", str(failure.exception))
        with self.assertRaisesRegex(ValueError, "overlapping"):
            read_candidate_pages([self.path, self.path])

    def test_page_and_candidate_limits(self):
        with self.assertRaises(ValueError):
            read_candidate_pages([])
        with self.assertRaises(ValueError):
            read_candidate_pages([self.path] * 21)
        with mock.patch("dairy_charging.read_candidates",
                        side_effect=[[{"sessionId": i} for i in range(5000)],
                                     [{"sessionId": 5000}]]):
            with self.assertRaisesRegex(ValueError, "too many"):
                read_candidate_pages(["one", "two"])

    def test_review_dialog_passes_selected_pages_to_one_window(self):
        dashboard = mock.Mock(selected=None)
        with (mock.patch("dairy_charging.filedialog.askopenfilenames", return_value=("one", "two")),
              mock.patch("dairy_charging.read_candidate_pages", return_value=[{"sessionId": 1}]) as read,
              mock.patch("dairy_charging.ReviewWindow") as window):
            Dashboard.open_review(dashboard)
        read.assert_called_once_with(("one", "two"))
        window.assert_called_once_with(dashboard, [{"sessionId": 1}])

    def test_fee_dialog_displays_selected_candidate_without_filling_form(self):
        review = mock.Mock()
        review.listbox.curselection.return_value = (1,)
        review.candidates = [{"fee_details": "first"}, {"fee_details": "second"}]
        review.variables = mock.MagicMock()
        with (mock.patch("dairy_charging.tk.Toplevel") as dialog,
              mock.patch("dairy_charging.ttk.Frame"),
              mock.patch("dairy_charging.ttk.Label"),
              mock.patch("dairy_charging.ttk.Button"),
              mock.patch("dairy_charging.scrolledtext.ScrolledText") as text):
            ReviewWindow.show_fees(review)
            text.return_value.insert.assert_called_once_with("1.0", "second")
            text.return_value.configure.assert_called_once_with(state="disabled")
            dialog.return_value.protocol.call_args.args[1]()
        review.window.grab_set.assert_called_once()
        review.variables.__getitem__.assert_not_called()

    def test_select_suggests_only_saved_vehicle_and_resets_review(self):
        review = mock.Mock()
        review.listbox.curselection.return_value = (0,)
        review.candidates = [{"vin": "SYNTHETIC-ID", "source_vehicle_identifier": "SYNTHETIC-ID",
                              "siteLocationName": "Example", "chargeStartDateTime": "Example start",
                              "chargeStopDateTime": "Example stop"}]
        review.variables = {key: mock.Mock() for key in
                            ("vehicle", "dairy", "direction", "date", "time", "location",
                             "odometer", "trip_miles", "energy", "rate", "paid")}
        review.dashboard.ledger.associated_vehicle.return_value = "Example car"
        ReviewWindow.select(review)
        review.variables["vehicle"].set.assert_has_calls([mock.call(""), mock.call("Example car")])
        for key in review.variables:
            if key != "vehicle":
                review.variables[key].set.assert_called_once_with("")
        review.confirmed.set.assert_called_once_with(False)
        self.assertIn("saved association", review.association_status.set.call_args.args[0])

    def test_association_change_requires_separate_confirmation_and_does_not_save_charge(self):
        review = mock.Mock()
        review.listbox.curselection.return_value = (0,)
        review.candidates = [{"source_vehicle_identifier": "SYNTHETIC-ID"}]
        review.variables = {"vehicle": mock.Mock()}
        review.variables["vehicle"].get.return_value = "Example car"
        with mock.patch("dairy_charging.messagebox.askyesno", return_value=False):
            ReviewWindow.associate_vehicle(review)
        review.dashboard.ledger.associate_vehicle.assert_not_called()
        with mock.patch("dairy_charging.messagebox.askyesno", return_value=True):
            ReviewWindow.associate_vehicle(review)
        review.dashboard.ledger.associate_vehicle.assert_called_once_with("SYNTHETIC-ID", "Example car")
        review.dashboard.ledger.save_reviewed.assert_not_called()
        review.confirmed.set.assert_called_once_with(False)
        with mock.patch("dairy_charging.messagebox.askyesno", return_value=True):
            ReviewWindow.forget_vehicle_association(review)
        review.dashboard.ledger.forget_vehicle_association.assert_called_once_with("SYNTHETIC-ID")
        review.dashboard.ledger.save_reviewed.assert_not_called()

    def test_association_lookup_failure_is_visible_and_redacted(self):
        review = mock.Mock()
        review.listbox.curselection.return_value = (0,)
        review.candidates = [{"vin": "SYNTHETIC-ID", "source_vehicle_identifier": "SYNTHETIC-ID",
                              "siteLocationName": "Example", "chargeStartDateTime": "Example",
                              "chargeStopDateTime": "Example"}]
        review.variables = {"vehicle": mock.Mock()}
        review.dashboard.ledger.associated_vehicle.side_effect = sqlite3.Error("SYNTHETIC-SECRET")
        with mock.patch("dairy_charging.messagebox.showerror") as error:
            ReviewWindow.select(review)
        error.assert_called_once()
        self.assertNotIn("SYNTHETIC", str(error.call_args))
        review.associate_button.configure.assert_called_with(state="disabled")

    def test_editing_a_reviewed_value_clears_its_confirmation(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        root.withdraw()
        ledger = Ledger(Path(self.temp.name) / "synthetic-ledger.sqlite3")
        self.addCleanup(ledger.close)
        self.path.write_text(json.dumps({"data": [{"sessionId": 1, "vin": "SYNTHETIC-ID"}],
                                         "totalResults": 1}))
        review = ReviewWindow(Dashboard(root, ledger), read_candidates(self.path))
        review.window.withdraw()
        review.confirmed.set(True)
        review.variables["vehicle"].set("Example car")
        self.assertFalse(review.confirmed.get())
        review.confirmed.set(True)
        review.variables["energy"].set("10")
        self.assertFalse(review.confirmed.get())
        self.assertEqual(ledger.rows(), [])


if __name__ == "__main__":
    unittest.main()
