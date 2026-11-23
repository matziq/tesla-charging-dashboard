import tempfile
import unittest
from pathlib import Path

from dairy_charging import Ledger, summarize, validate


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


if __name__ == "__main__":
    unittest.main()
