import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import refresh_dashboard
from refresh_dashboard import DATA_BLOCK, load_rows, render_preview


HEADER = ["Date", "Vehicle", "Location", "State", "kWh", "Cost", "Category"]
ROW = ["2026-06-01", "Car", "Example, AZ", "AZ", "12.5", "4.20", "WORK"]


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "input.csv"
        self.addCleanup(self.temp.cleanup)

    def write_csv(self, rows, header=HEADER):
        with self.path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(rows)

    def test_imports_complete_rows_and_renders_without_changing_template(self):
        self.write_csv([ROW, ROW])
        rows = load_rows(self.path)
        template = "before\n    const data = [\n      []\n    ];\nafter"
        result = render_preview(template, rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["2026-06-01", "Car", "Example, AZ", "AZ", 12.5, 4.2, "WORK"])
        self.assertIn('"Example, AZ"', result)
        self.assertTrue(result.startswith("before\n"))
        self.assertTrue(result.endswith("\nafter"))
        self.assertEqual(len(DATA_BLOCK.findall(template)), 1)

    def test_accepts_dashboard_export_rate_column(self):
        self.write_csv([ROW[:6] + ["0.3360", ROW[6]]], HEADER[:6] + ["$/kWh", HEADER[6]])
        self.assertEqual(load_rows(self.path)[0][-1], "WORK")

    def test_rejects_bad_rows(self):
        cases = [
            ROW[:1] + ["<img src=x>"] + ROW[2:],
            ["2026-02-30"] + ROW[1:],
            ROW[:4] + ["NaN"] + ROW[5:],
            ROW[:5] + ["-1"] + ROW[6:],
            ROW[:6] + ["UNKNOWN"],
            ROW[:6],
            ROW + ["extra"],
        ]
        for row in cases:
            with self.subTest(row=row):
                self.write_csv([row])
                with self.assertRaises(ValueError):
                    load_rows(self.path)

    def test_rejects_empty_and_mismatched_headers(self):
        self.write_csv([])
        with self.assertRaises(ValueError):
            load_rows(self.path)
        self.write_csv([ROW], HEADER[:-1] + ["Classification"])
        with self.assertRaises(ValueError):
            load_rows(self.path)

    def test_rejects_missing_or_ambiguous_template(self):
        with self.assertRaises(ValueError):
            render_preview("no data", [])
        block = "    const data = [\n      []\n    ];"
        with self.assertRaises(ValueError):
            render_preview(block + "\n" + block, [])

    def test_retired_cli_does_not_read_or_write_data(self):
        self.write_csv([ROW])
        with patch.object(refresh_dashboard, "load_rows") as load:
            with self.assertRaisesRegex(SystemExit, "preview is retired"):
                refresh_dashboard.main()
        load.assert_not_called()
        self.assertFalse((Path(self.temp.name) / ".private").exists())


if __name__ == "__main__":
    unittest.main()
