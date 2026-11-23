import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dairy_charging
import probe_tesla_pagination as probe
import stage_tesla_history as stage


def page(start, count, total):
    return json.dumps({"data": [{"sessionId": i, "vin": "SYNTHETIC-PRIVATE"}
                                for i in range(start, start + count)],
                       "totalResults": total}).encode()


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_stage(self, responses, cap=3):
        output = io.StringIO()
        with patch.object(probe, "ROOT", self.root), \
             patch.object(stage, "fetch_staged_page", side_effect=responses) as fetch, \
             contextlib.redirect_stdout(output):
            status = stage.stage("SYNTHETIC-TOKEN", cap)
        directories = list((self.root / ".private").glob("history-stage-*"))
        self.assertEqual(len(directories), 1)
        return status, output.getvalue(), fetch, directories[0]

    def test_matched_total_stages_reviewable_pages_without_claiming_completeness(self):
        status, output, fetch, directory = self.run_stage([page(1, 25, 30), page(26, 5, 30)])
        self.assertEqual(status, 0)
        self.assertEqual([call.args for call in fetch.call_args_list],
                         [("SYNTHETIC-TOKEN", 1), ("SYNTHETIC-TOKEN", 2)])
        self.assertEqual(len(dairy_charging.read_candidate_pages(
            [directory / "page-1.bin", directory / "page-2.bin"])), 30)
        self.assertIn("matches the reported total", output)
        self.assertIn("complete history are unverified", output)
        self.assertNotIn("SYNTHETIC-PRIVATE", output)
        self.assertNotIn("SYNTHETIC-TOKEN", output)
        self.assertNotIn("totalResults", output)

    def test_empty_history_match_is_still_unverified(self):
        status, output, fetch, directory = self.run_stage([page(1, 0, 0)])
        self.assertEqual(status, 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(dairy_charging.read_candidates(directory / "page-1.bin"), [])
        self.assertIn("complete history are unverified", output)

    def test_cap_stops_without_requesting_another_page(self):
        status, output, fetch, directory = self.run_stage([page(1, 25, 100)], cap=1)
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue((directory / "page-1.bin").is_file())
        self.assertIn("request cap was reached", output)
        self.assertIn("incomplete or unverified", output)

    def test_consistency_failures_stop_without_staging_failing_page(self):
        cases = (
            (page(26, 25, 101), "totals changed"),
            (page(25, 25, 100), "repeated across pages"),
            (page(26, 2, 100), "short or empty page"),
            (page(26, 0, 100), "short or empty page"),
            (page(26, 26, 100), "exceeded the requested size"),
            (b'{"data":["SYNTHETIC-PRIVATE"]', "malformed"),
            (json.dumps({"data": [{"sessionId": 2}, {"sessionId": 2}],
                         "totalResults": 100}).encode(), "repeated session identifiers"),
            (json.dumps({"data": [{"sessionId": 26, "fees": ["SYNTHETIC-PRIVATE"]}],
                         "totalResults": 100}).encode(), "malformed"),
        )
        for body, message in cases:
            with self.subTest(case=message), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with patch.object(probe, "ROOT", root), \
                     patch.object(stage, "fetch_staged_page", side_effect=[page(1, 25, 100), body]) as fetch, \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    status = stage.stage("SYNTHETIC-TOKEN", 3)
                self.assertEqual(status, 2)
                self.assertEqual(fetch.call_count, 2)
                self.assertIn(message, out.getvalue())
                self.assertNotIn("SYNTHETIC-PRIVATE", out.getvalue())
                folder = next((root / ".private").iterdir())
                self.assertTrue((folder / "page-1.bin").is_file())
                self.assertFalse((folder / "page-2.bin").exists())

    def test_more_rows_than_total_is_rejected(self):
        status, output, fetch, directory = self.run_stage([page(1, 25, 40), page(26, 20, 40)])
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("exceeded the reported total", output)
        self.assertFalse((directory / "page-2.bin").exists())

    def test_second_request_failure_preserves_first_without_retry(self):
        status, output, fetch, directory = self.run_stage(
            [page(1, 25, 100), ValueError("Tesla returned HTTP 403; no response was saved")])
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("HTTP 403", output)
        self.assertTrue((directory / "page-1.bin").is_file())
        self.assertFalse((directory / "page-2.bin").exists())

    def test_http_and_unknown_failures_do_not_leak_or_retry(self):
        for failure, expected in (
            (ValueError("Tesla returned HTTP 403; no response was saved"), "HTTP 403"),
            (ValueError("SYNTHETIC-TOKEN SYNTHETIC-PRIVATE"), "request or response failed"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder, \
                 patch.object(probe, "ROOT", Path(folder)), \
                 patch.object(stage, "fetch_staged_page", side_effect=[failure]) as fetch, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                status = stage.stage("SYNTHETIC-TOKEN", 3)
            self.assertEqual(status, 2)
            self.assertEqual(fetch.call_count, 1)
            self.assertIn(expected, out.getvalue())
            self.assertNotIn("SYNTHETIC", out.getvalue())

    def test_invalid_cap_does_not_create_directory_or_request(self):
        for cap in (0, 6, True, "2"):
            with self.subTest(cap=cap), patch.object(stage, "private_directory") as directory, \
                 patch.object(stage, "fetch_staged_page") as fetch, self.assertRaises(ValueError):
                stage.stage("token", cap)
            directory.assert_not_called()
            fetch.assert_not_called()

    def test_save_failure_stops_and_warns_of_possible_partial_file(self):
        with patch.object(probe, "ROOT", self.root), \
             patch.object(stage, "fetch_staged_page", return_value=page(1, 25, 100)) as fetch, \
             patch.object(stage, "save_page", side_effect=ValueError("SYNTHETIC")) as save, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            status = stage.stage("token", 3)
        self.assertEqual(status, 2)
        self.assertEqual(fetch.call_count, 1)
        save.assert_called_once()
        self.assertIn("partial file may remain", out.getvalue())
        self.assertNotIn("SYNTHETIC", out.getvalue())

    def test_user_choices_and_both_confirmation_gates(self):
        cases = (([""], False, False), (["6"], False, False), (["2", ""], False, False),
                 (["2", "CONTINUE", ""], True, False), (["2", "CONTINUE", "FETCH"], True, True))
        for replies, authorized, fetched in cases:
            with self.subTest(replies=replies), \
                 patch.object(stage, "require_terminal"), \
                 patch("builtins.input", side_effect=replies), \
                 patch.object(stage.auth, "application_credentials", return_value=("client", "secret")) as creds, \
                 patch.object(stage.auth, "wait_for_code", return_value="code") as auth, \
                 patch.object(stage.auth, "exchange_code", return_value="token") as exchange, \
                 patch.object(stage, "stage", return_value=0) as go, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                if fetched:
                    self.assertEqual(stage.run(), 0)
                else:
                    with self.assertRaises(ValueError):
                        stage.run()
            self.assertEqual(auth.called, authorized)
            self.assertEqual(creds.called, authorized)
            self.assertEqual(go.called, fetched)
            if authorized:
                self.assertFalse(auth.call_args.kwargs["offline"])
                self.assertFalse(exchange.call_args.kwargs["offline"])
                self.assertIn("at most 2 read-only history GETs", out.getvalue())
                self.assertIn("charging commands", out.getvalue())
                self.assertIn("unencrypted", out.getvalue())
            if fetched:
                go.assert_called_once_with("token", 2)
                self.assertIn("at most 2 potentially billable", out.getvalue())

    def test_noninteractive_terminal_stops_before_prompt_or_authorization(self):
        with patch.object(stage, "require_terminal", side_effect=stage.PrivateInputError("terminal required")), \
             patch("builtins.input") as prompt, \
             patch.object(stage.auth, "application_credentials") as credentials:
            with self.assertRaises(stage.PrivateInputError):
                stage.run()
        prompt.assert_not_called()
        credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
