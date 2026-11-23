import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tesla_token_store import TokenStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI and locking required")
class WindowsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TokenStore(self.tmp.name)

    def test_round_trip_encrypts_synthetic_credentials(self):
        with self.store.locked():
            self.store.write("synthetic-client", "synthetic-refresh")
            data = self.store.path.read_bytes()
            self.assertNotIn(b"synthetic-client", data)
            self.assertNotIn(b"synthetic-refresh", data)
            self.assertEqual(self.store.read(), ("synthetic-client", "synthetic-refresh", "ready"))

    def test_other_process_cannot_acquire_same_lock(self):
        script = (
            "from tesla_token_store import TokenStore\n"
            "import sys\n"
            "try:\n"
            "    with TokenStore(sys.argv[1]).locked(): pass\n"
            "except ValueError: sys.exit(7)\n"
        )
        with self.store.locked():
            result = subprocess.run([sys.executable, "-c", script, self.tmp.name],
                                    capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_failed_atomic_replace_preserves_pending_state(self):
        with self.store.locked():
            self.store.write("client", "old-refresh", state="pending")
            original = self.store.path.read_bytes()
            with patch("tesla_token_store.os.replace", side_effect=OSError("sensitive")):
                with self.assertRaises(ValueError) as error:
                    self.store.write("client", "new-refresh")
            self.assertNotIn("sensitive", str(error.exception))
            self.assertEqual(self.store.path.read_bytes(), original)
            self.assertEqual(self.store.read(), ("client", "old-refresh", "pending"))
            self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()),
                             ["offline-token.bin", "offline-token.lock"])

    def test_invalid_or_tampered_record_is_rejected_without_echo(self):
        with self.store.locked():
            self.store.path.write_bytes(b"synthetic-secret-not-dpapi")
            with self.assertRaises(ValueError) as error:
                self.store.read()
            self.assertNotIn("synthetic-secret", str(error.exception))
            with self.assertRaises(ValueError):
                self.store.write("client", "token\n")


if __name__ == "__main__":
    unittest.main()
