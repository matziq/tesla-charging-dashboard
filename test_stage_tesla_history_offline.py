import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import authorize_tesla as auth
import probe_tesla_pagination as probe
import stage_tesla_history as staging
import stage_tesla_history_offline as offline
from tesla_token_store import TokenStore


ACKS = ["--accept-offline-scope-and-billing",
        "--accept-unencrypted-storage-and-unverified-pagination"]


class CommandTests(unittest.TestCase):
    def test_requires_explicit_cap_and_both_acknowledgments_before_store_access(self):
        for args in ([], ["--max-pages", "2"], ["--max-pages", "2", ACKS[0]],
                     ["--max-pages", "2", ACKS[1]], ["--max-pages", "6", *ACKS],
                     ["--max-pages", "0", *ACKS], ["SYNTHETIC-SECRET"]):
            with self.subTest(case=len(args)), \
                 patch.object(offline, "TokenStore") as store, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as failure:
                    offline.main(args)
            self.assertEqual(failure.exception.code, 2)
            store.assert_not_called()
            self.assertNotIn("SYNTHETIC-SECRET", err.getvalue())

    def test_valid_acknowledgments_pass_exact_cap_without_prompt(self):
        with patch.object(offline, "TokenStore", return_value="store"), \
             patch.object(offline, "run", return_value=2) as run, \
             patch("builtins.input") as prompt:
            self.assertEqual(offline.main(["--max-pages", "3", *ACKS]), 2)
        run.assert_called_once_with("store", 3)
        prompt.assert_not_called()

    def test_errors_are_redacted_and_invalid_cap_fails_before_store(self):
        with patch.object(offline, "TokenStore", return_value="store"), \
             patch.object(offline, "run", side_effect=ValueError("SYNTHETIC-SECRET")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(offline.main(["--max-pages", "1", *ACKS]), 1)
        self.assertNotIn("SYNTHETIC-SECRET", output.getvalue())
        self.assertIn("No automatic retry", output.getvalue())
        with patch.object(auth, "with_refreshed_access") as refresh:
            with self.assertRaises(ValueError):
                offline.run("store", 6)
        refresh.assert_not_called()

    def test_pending_and_missing_access_token_are_visible_without_private_details(self):
        cases = (
            ("A previous refresh has an uncertain outcome; manual recovery is required", "pending"),
            ("Tesla token response did not contain an access token", "no access token"),
            ("Tesla token exchange returned HTTP 401. Refresh outcome may be uncertain; do not retry automatically",
             "HTTP 401"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected), \
                 patch.object(offline, "TokenStore", return_value="store"), \
                 patch.object(offline, "run", side_effect=ValueError(error)), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(offline.main(["--max-pages", "1", *ACKS]), 1)
            self.assertIn(expected, output.getvalue())


@unittest.skipUnless(os.name == "nt", "Windows DPAPI and locking required")
class OfflineStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TokenStore(self.root / "store")
        with self.store.locked():
            self.store.write("SYNTHETIC-CLIENT", "SYNTHETIC-OLD")

    def test_rotation_persists_before_get_and_lock_covers_entire_stage(self):
        body = json.dumps({"data": [{"sessionId": i, "vin": "SYNTHETIC-PRIVATE"}
                                    for i in range(25)], "totalResults": 100}).encode()
        child = (
            "from tesla_token_store import TokenStore\n"
            "import sys\n"
            "try:\n"
            "    with TokenStore(sys.argv[1]).locked(): pass\n"
            "except ValueError: sys.exit(7)\n"
        )

        def token_request(fields):
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-OLD", "pending"))
            self.assertEqual(fields, {"grant_type": "refresh_token",
                                      "client_id": "SYNTHETIC-CLIENT",
                                      "refresh_token": "SYNTHETIC-OLD"})
            return {"access_token": "SYNTHETIC-ACCESS", "refresh_token": "SYNTHETIC-NEW"}

        def fetch(token, number):
            self.assertEqual((token, number), ("SYNTHETIC-ACCESS", 1))
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-NEW", "ready"))
            result = subprocess.run([sys.executable, "-B", "-c", child, str(self.store.directory)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            return body

        with patch.object(probe, "ROOT", self.root), \
             patch.object(auth, "token_request", side_effect=token_request) as renew, \
             patch.object(staging, "fetch_staged_page", side_effect=fetch) as history, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = offline.run(self.store, 1)
        self.assertEqual(status, 2)
        renew.assert_called_once()
        history.assert_called_once()
        self.assertIn("request cap was reached", output.getvalue())
        self.assertIn("incomplete or unverified", output.getvalue())
        self.assertNotIn("SYNTHETIC", output.getvalue())
        with self.store.locked():
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-NEW", "ready"))

    def test_pending_or_failed_refresh_prevents_history_and_retry(self):
        with patch.object(auth, "token_request", side_effect=ValueError("synthetic failure")) as request, \
             patch.object(staging, "stage") as stage, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "uncertain"):
                offline.run(self.store, 1)
            with self.assertRaisesRegex(ValueError, "manual recovery"):
                offline.run(self.store, 1)
        request.assert_called_once()
        stage.assert_not_called()
        with self.store.locked():
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-OLD", "pending"))

    def test_pending_write_failure_stops_before_token_exchange(self):
        with patch.object(self.store, "write", side_effect=ValueError("synthetic failure")), \
             patch.object(auth, "token_request") as request, \
             patch.object(staging, "stage") as stage, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                offline.run(self.store, 1)
        request.assert_not_called()
        stage.assert_not_called()
        with self.store.locked():
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-OLD", "ready"))

    def test_rotation_write_failure_leaves_pending_and_blocks_future_fetch(self):
        original_write = self.store.write

        def write(client, token, state="ready"):
            if token == "SYNTHETIC-NEW":
                raise ValueError("synthetic write failure")
            return original_write(client, token, state)

        with patch.object(self.store, "write", side_effect=write), \
             patch.object(auth, "token_request", return_value={
                 "access_token": "SYNTHETIC-ACCESS", "refresh_token": "SYNTHETIC-NEW"}) as request, \
             patch.object(staging, "stage") as stage, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "uncertain"):
                offline.run(self.store, 1)
            with self.assertRaisesRegex(ValueError, "manual recovery"):
                offline.run(self.store, 1)
        request.assert_called_once()
        stage.assert_not_called()
        with self.store.locked():
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-OLD", "pending"))

    def test_missing_access_token_persists_rotation_but_does_not_fetch(self):
        with patch.object(auth, "token_request", return_value={"refresh_token": "SYNTHETIC-NEW"}), \
             patch.object(staging, "stage") as stage, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "access token"):
                offline.run(self.store, 1)
        stage.assert_not_called()
        with self.store.locked():
            self.assertEqual(self.store.read(), ("SYNTHETIC-CLIENT", "SYNTHETIC-NEW", "ready"))


if __name__ == "__main__":
    unittest.main()
