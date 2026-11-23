import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import compare_tesla_history_pages as compare


def page(ids, total=4):
    return {"data": [{"sessionId": n, "vin": "synthetic-secret"} for n in ids],
            "totalResults": total}


class ComparePagesTests(unittest.TestCase):
    def test_comparison_distinguishes_overlap_without_exposing_values(self):
        for ids, expected in (
            ([11, 12], "identical identifiers in the same order"),
            ([12, 11], "identical identifiers in a different order"),
            ([13, 14], "no identifiers overlap"),
            ([12, 13], "some identifiers overlap"),
            ([], "at least one page is empty"),
        ):
            with self.subTest(case=expected):
                totals, overlap = compare.compare_pages((4, [11, 12]), (4, ids))
                self.assertEqual((totals, overlap), ("match", expected))
        self.assertEqual(compare.compare_pages((4, [11]), (5, [12]))[0], "changed")

    def test_cli_prints_only_safe_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            first.write_text(json.dumps(page([11, 12])), encoding="utf-8")
            second.write_text(json.dumps(page([13, 14])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                compare.main([str(first), str(second)])
            text = output.getvalue()
            self.assertIn("no identifiers overlap", text)
            self.assertIn("does not establish", text)
            for value in ("synthetic-secret", "11", "12", "13", "14", str(first), str(second)):
                self.assertNotIn(value, text)

    def test_rejects_bad_pages_without_outputting_contents(self):
        bodies = (
            b'{"data":[{"sessionId":11},{"sessionId":11}],"totalResults":2}',
            b'{"data":[{"sessionId":"synthetic-secret"}],"totalResults":1}',
            b'{"data":[{"sessionId":1}],"totalResults":true}',
            b'{"data":[{"sessionId":1}],"totalResults":0}',
            b'{"data":[],"totalResults":0,"totalResults":1}',
            b'{"data":"synthetic-secret","totalResults":1}',
            b'{"data":["synthetic-secret"]',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            for body in bodies:
                with self.subTest(index=bodies.index(body)):
                    path.write_bytes(body)
                    with self.assertRaises(ValueError) as failure:
                        compare.read_page(path)
                    self.assertNotIn("synthetic-secret", str(failure.exception))
                    self.assertNotIn(str(path), str(failure.exception))

    def test_file_read_failure_is_sanitized(self):
        with self.assertRaises(ValueError) as failure:
            compare.read_page(Path("missing-synthetic-secret"))
        self.assertNotIn("synthetic-secret", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
