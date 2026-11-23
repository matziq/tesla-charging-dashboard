import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_tesla_page_structure as check


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = [Path(self.temp.name) / "private-one.bin",
                      Path(self.temp.name) / "private-two.bin"]

    def write_pages(self, pages):
        for path, page in zip(self.paths, pages):
            path.write_text(json.dumps(page), encoding="utf-8")

    def run_check(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = check.main(args)
        return status, output.getvalue()

    def test_synthetic_pages_report_types_and_totals_without_values_or_writes(self):
        first = {"data": [{"sessionId": 17891, "vin": "SYNTHETIC-VIN",
                           "siteLocationName": "SYNTHETIC-LOCATION",
                           "chargeStartDateTime": "SYNTHETIC-DATE",
                           "chargeStopDateTime": "SYNTHETIC-STOP",
                           "fees": [{"uom": "SYNTHETIC-UNIT", "rateBase": 0.123,
                                     "usageBase": 23, "totalDue": 6.78, "isPaid": True}]}],
                 "totalResults": 2}
        second = {"data": [{"sessionId": 17892, "vin": "SYNTHETIC-OTHER", "fees": []}],
                  "totalResults": 2}
        self.write_pages([first, second])
        before = [path.read_bytes() for path in self.paths]
        with mock.patch("socket.socket", side_effect=AssertionError("network access")):
            status, text = self.run_check([str(path) for path in self.paths])
        self.assertEqual(status, 0)
        self.assertIn("Combined row count equals both reported totals: yes", text)
        self.assertIn("Identifiers overlap across pages: no", text)
        self.assertIn("Vehicle identifier (nonempty text): all", text)
        self.assertIn("Location (nonempty text): some", text)
        self.assertIn("Odometer candidate 'odometer' (number): none", text)
        self.assertIn("Unit of measure (nonempty text): all", text)
        self.assertIn("Paid flag (boolean): all", text)
        self.assertEqual(before, [path.read_bytes() for path in self.paths])
        for private in ("SYNTHETIC", "17891", "17892", "0.123", "6.78", *map(str, self.paths)):
            self.assertNotIn(private, text)

    def test_empty_pages_and_absent_fee_entries_are_not_all_present(self):
        self.write_pages([{"data": [], "totalResults": 0}, {"data": [], "totalResults": 0}])
        status, text = self.run_check([str(p) for p in self.paths])
        self.assertEqual(status, 0)
        self.assertIn("Identifier overlap check: not applicable", text)
        self.assertIn("Vehicle identifier (nonempty text): not applicable (no records)", text)
        self.assertIn("Paid flag (boolean): not applicable (no records)", text)
        self.assertIn("Matching totals do not prove complete", text)

    def test_unequal_totals_and_overlapping_identifiers(self):
        self.write_pages([{"data": [{"sessionId": 7}], "totalResults": 10},
                          {"data": [{"sessionId": 7}], "totalResults": 10}])
        status, text = self.run_check([str(p) for p in self.paths])
        self.assertEqual(status, 0)
        self.assertIn("Combined row count equals both reported totals: no", text)
        self.assertIn("Identifiers overlap across pages: yes", text)

    def test_bad_json_bad_shape_and_inaccessible_files_fail_safely(self):
        self.paths[1].write_text('{"data":[],"totalResults":0}', encoding="utf-8")
        for body in (b'{"private":"SYNTHETIC"', b'{"data":[],"totalResults":0,"totalResults":1}',
                     b'{"data":[{"fees":["SYNTHETIC"]}],"totalResults":1}',
                     b'{"data":[],"totalResults":true}', b'{"data":[],"totalResults":NaN}'):
            self.paths[0].write_bytes(body)
            status, text = self.run_check([str(p) for p in self.paths])
            self.assertEqual(status, 1)
            self.assertIn("Check failed:", text)
            self.assertNotIn("SYNTHETIC", text)
            self.assertNotIn(str(self.paths[0]), text)
            self.assertNotIn("Combined row count", text)
        with mock.patch.object(Path, "open", side_effect=PermissionError("SYNTHETIC-SECRET")):
            status, text = self.run_check([str(p) for p in self.paths])
        self.assertEqual(status, 1)
        self.assertNotIn("SYNTHETIC-SECRET", text)

    def test_selection_cancel_and_bad_argument_count_do_not_read_files(self):
        with mock.patch.object(check, "choose_pages", return_value=None), \
             mock.patch.object(check, "read_page") as read:
            status, text = self.run_check([])
        self.assertEqual(status, 1)
        self.assertIn("Canceled", text)
        read.assert_not_called()
        status, text = self.run_check(["SYNTHETIC-PATH"])
        self.assertEqual(status, 1)
        self.assertNotIn("SYNTHETIC-PATH", text)

    def test_boolean_is_not_a_number_and_invalid_time_is_not_text(self):
        self.write_pages([{"data": [{"energy": True, "vin": "", "fees": [{"rateBase": True}]}],
                           "totalResults": 2},
                          {"data": [{}], "totalResults": 2}])
        status, text = self.run_check([str(p) for p in self.paths])
        self.assertEqual(status, 0)
        self.assertIn("Energy candidate 'energy' (number): none", text)
        self.assertIn("Base rate (number): none", text)
        self.assertIn("Vehicle identifier (nonempty text): none", text)


if __name__ == "__main__":
    unittest.main()
