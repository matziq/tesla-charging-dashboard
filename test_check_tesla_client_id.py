import contextlib
import io
import unittest
from unittest.mock import patch

import check_tesla_client_id as check


class ClientIdCheckTests(unittest.TestCase):
    def run_check(self, entries):
        output = io.StringIO()
        with patch.object(check, "read_masked", side_effect=entries), \
             patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
             patch("socket.socket", side_effect=AssertionError("unexpected network access")), \
             contextlib.redirect_stdout(output):
            check.main()
        return output.getvalue()

    def test_matching_uuid_is_private_and_never_uses_files_or_network(self):
        client_id = "12345678-1234-1234-1234-123456789abc"
        output = self.run_check([client_id, client_id])
        self.assertIn("Entries match exactly: yes", output)
        self.assertIn("First entry changed by the registration helper's trimming: no", output)
        self.assertIn("Either entry contains control or invisible format characters: no", output)
        self.assertIn("First entry after trimming is UUID-shaped: yes", output)
        self.assertNotIn(client_id, output)

    def test_mismatch_whitespace_and_invisible_character_are_reported(self):
        pasted = " 12345678-1234-1234-1234-123456789abc "
        typed = "12345678-1234-1234-1234-123456789ab\u200bc"
        output = self.run_check([pasted, typed])
        self.assertIn("Entries match exactly: no", output)
        self.assertIn("First entry changed by the registration helper's trimming: yes", output)
        self.assertIn("Either entry contains control or invisible format characters: yes", output)
        self.assertIn("First entry after trimming is UUID-shaped: yes", output)
        self.assertNotIn(pasted, output)
        self.assertNotIn(typed, output)

    def test_literal_ctrl_v_is_not_treated_as_a_paste(self):
        client_id = "12345678-1234-1234-1234-123456789abc"
        output = self.run_check(["\x16" + client_id, client_id])
        self.assertIn("Entries match exactly: no", output)
        self.assertIn("Either entry contains control or invisible format characters: yes", output)
        self.assertIn("First entry after trimming is UUID-shaped: no", output)
        self.assertNotIn(client_id, output)

    def test_empty_and_malformed_entries_do_not_pass(self):
        for value in ("", "not-a-uuid", "12345678-1234-1234-1234-123456789ab\t"):
            with self.subTest(value=value):
                output = self.run_check([value, value])
                self.assertIn("First entry after trimming is UUID-shaped: no", output)
                if not value:
                    self.assertIn("Entries match exactly: no", output)

    def test_insecure_terminal_is_rejected_without_printing_input(self):
        with patch.object(check, "read_masked", side_effect=check.PrivateInputError("a private interactive terminal is required")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as failure:
                check.main()
        self.assertEqual(str(failure.exception), "Check failed: a private interactive terminal is required")
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
