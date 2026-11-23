import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import probe_tesla_pagination as probe


def page(ids):
    return json.dumps({"data": [{"sessionId": i, "vin": "synthetic-private"} for i in ids],
                       "totalResults": 10}).encode()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.terminal = Mock()
        self.terminal.isatty.return_value = True
        self.stderr = Mock()
        self.stderr.isatty.return_value = True

    def run_mocked(self, confirmations, responses):
        output = io.StringIO()
        with patch.object(probe, "ROOT", self.root), \
             patch.object(probe.sys, "stdin", self.terminal), \
             patch.object(probe.sys, "__stdin__", self.terminal), \
             patch.object(probe.sys, "stderr", self.stderr), \
             patch("builtins.input", side_effect=confirmations), \
             patch.object(probe.auth, "application_credentials", return_value=("synthetic-client", "synthetic-secret")) as credentials, \
             patch.object(probe.auth, "wait_for_code", return_value="synthetic-code") as authorize, \
             patch.object(probe.auth, "exchange_code", return_value="synthetic-token") as exchange, \
             patch.object(probe, "fetch_probe_page", side_effect=responses) as fetch, \
             contextlib.redirect_stdout(output):
            probe.run("synthetic-path")
        return output.getvalue(), credentials, authorize, exchange, fetch

    def test_two_confirmed_requests_save_and_compare_without_disclosing_secrets(self):
        output, credentials, authorize, exchange, fetch = self.run_mocked(
            ["CONTINUE", "FETCH"], [page([11, 12]), page([13, 14])])
        credentials.assert_called_once_with("synthetic-path")
        authorize.assert_called_once()
        exchange.assert_called_once()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([call.args for call in fetch.call_args_list],
                         [("synthetic-token", 1), ("synthetic-token", 2)])
        self.assertIn("no identifiers overlap", output)
        self.assertIn("do not prove", output)
        for value in ("synthetic-client", "synthetic-secret", "synthetic-code", "synthetic-token",
                      "synthetic-private", "synthetic-path"):
            self.assertNotIn(value, output)
        directories = list((self.root / ".private").iterdir())
        self.assertEqual(len(directories), 1)
        self.assertEqual((directories[0] / "page-1.bin").read_bytes(), page([11, 12]))
        self.assertEqual((directories[0] / "page-2.bin").read_bytes(), page([13, 14]))

    def test_cancel_before_auth_or_requests(self):
        for confirmations, auth_expected in (([""], False), (["CONTINUE", ""], True)):
            with self.subTest(auth_expected=auth_expected), \
                 patch.object(probe.sys, "stdin", self.terminal), \
                 patch.object(probe.sys, "__stdin__", self.terminal), \
                 patch.object(probe.sys, "stderr", self.stderr), \
                 patch("builtins.input", side_effect=confirmations), \
                 patch.object(probe.auth, "application_credentials", return_value=("client", "secret")), \
                 patch.object(probe.auth, "wait_for_code", return_value="code") as authorize, \
                 patch.object(probe.auth, "exchange_code", return_value="token"), \
                 patch.object(probe, "private_directory") as directory, \
                 patch.object(probe, "fetch_probe_page") as fetch, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "Canceled"):
                    probe.run()
            self.assertEqual(authorize.called, auth_expected)
            directory.assert_not_called()
            fetch.assert_not_called()

    def test_noninteractive_rejected_before_authorization(self):
        self.terminal.isatty.return_value = False
        with patch.object(probe.sys, "stdin", self.terminal), \
             patch.object(probe.sys, "__stdin__", self.terminal), \
             patch.object(probe.auth, "application_credentials") as credentials:
            with self.assertRaisesRegex(probe.PrivateInputError, "interactive"):
                probe.run()
        credentials.assert_not_called()

    def test_first_failure_prevents_second_request_and_redacts_error_data(self):
        with patch.object(probe, "ROOT", self.root), \
             patch.object(probe.sys, "stdin", self.terminal), \
             patch.object(probe.sys, "__stdin__", self.terminal), \
             patch.object(probe.sys, "stderr", self.stderr), \
             patch("builtins.input", side_effect=["CONTINUE", "FETCH"]), \
             patch.object(probe.auth, "application_credentials", return_value=("client", "secret")), \
             patch.object(probe.auth, "wait_for_code", return_value="code"), \
             patch.object(probe.auth, "exchange_code", return_value="token"), \
             patch.object(probe, "fetch_probe_page", return_value=b'{"private":"synthetic-secret"}') as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError) as failure:
                probe.run()
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("synthetic-secret", str(failure.exception))
        self.assertIn("No automatic retry", str(failure.exception))

    def test_write_failure_removes_partial_page(self):
        with patch.object(probe.os, "fsync", side_effect=OSError("synthetic-private")):
            with self.assertRaises(ValueError) as failure:
                probe.save_page(self.root, 1, b"synthetic-private")
        self.assertNotIn("synthetic-private", str(failure.exception))
        self.assertFalse((self.root / "page-1.bin").exists())

    def test_existing_page_is_not_overwritten_on_save_failure(self):
        existing = self.root / "page-1.bin"
        existing.write_bytes(b"existing-synthetic")
        with self.assertRaises(ValueError):
            probe.save_page(self.root, 1, b"new-synthetic")
        self.assertEqual(existing.read_bytes(), b"existing-synthetic")

    def test_linked_private_directory_is_rejected(self):
        with patch.object(probe, "ROOT", self.root), \
             patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "non-link"):
                probe.private_directory()
        self.assertEqual(list((self.root / ".private").iterdir()), [])

    def test_second_request_failure_preserves_first_without_retry(self):
        with patch.object(probe, "ROOT", self.root), \
             patch.object(probe.sys, "stdin", self.terminal), \
             patch.object(probe.sys, "__stdin__", self.terminal), \
             patch.object(probe.sys, "stderr", self.stderr), \
             patch("builtins.input", side_effect=["CONTINUE", "FETCH"]), \
             patch.object(probe.auth, "application_credentials", return_value=("client", "secret")), \
             patch.object(probe.auth, "wait_for_code", return_value="code"), \
             patch.object(probe.auth, "exchange_code", return_value="token"), \
             patch.object(probe, "fetch_probe_page", side_effect=[page([11]), ValueError("Tesla returned HTTP 403; no response was saved")]) as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                probe.run()
        self.assertEqual(fetch.call_count, 2)
        saved = list((self.root / ".private").glob("pagination-probe-*/page-1.bin"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), page([11]))


if __name__ == "__main__":
    unittest.main()
