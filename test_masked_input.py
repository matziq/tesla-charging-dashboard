import sys
import types
import unittest
from unittest.mock import Mock, patch

import masked_input


class MaskedInputTests(unittest.TestCase):
    def test_windows_paste_and_backspace_are_masked(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        console = types.SimpleNamespace(
            getwch=Mock(side_effect=list("abc\bde\r")),
            putwch=Mock(),
        )
        with patch.object(sys, "stdin", terminal), patch.object(sys, "__stdin__", terminal), \
             patch.object(sys, "stderr", stderr), patch.object(sys, "version_info", (3, 12)), \
             patch.object(masked_input.os, "name", "nt"), \
             patch.dict(sys.modules, {"msvcrt": console}):
            value = masked_input.read_masked("ID: ")
        self.assertEqual(value, "abde")
        rendered = "".join(call.args[0] for call in console.putwch.call_args_list)
        self.assertEqual(rendered, "ID: ***\b \b**\r\n")
        self.assertNotIn("abc", rendered)
        self.assertNotIn(value, rendered)

    def test_control_and_invisible_characters_are_rejected(self):
        for value in ("\x16", "\t", "\u200b", "\x00"):
            with self.subTest(value=repr(value)):
                write = Mock()
                with self.assertRaises(masked_input.PrivateInputError) as failure:
                    masked_input._read_characters("ID: ", Mock(side_effect=["a", value]), write)
                self.assertIn("Unsupported control or invisible character", str(failure.exception))
                self.assertEqual("".join(call.args[0] for call in write.call_args_list), "ID: *\n")

    def test_windows_extended_key_is_rejected(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        console = types.SimpleNamespace(getwch=Mock(return_value="\xe0"), putwch=Mock())
        with patch.object(sys, "stdin", terminal), patch.object(sys, "__stdin__", terminal), \
             patch.object(sys, "stderr", stderr), patch.object(sys, "version_info", (3, 12)), \
             patch.object(masked_input.os, "name", "nt"), \
             patch.dict(sys.modules, {"msvcrt": console}):
            with self.assertRaisesRegex(masked_input.PrivateInputError, "Unsupported special key or character"):
                masked_input.read_masked("ID: ")

    def test_noninteractive_input_is_rejected_before_reading(self):
        terminal = Mock()
        terminal.isatty.return_value = False
        with patch.object(sys, "stdin", terminal), patch.object(sys, "__stdin__", terminal):
            with self.assertRaisesRegex(masked_input.PrivateInputError, "private interactive terminal"):
                masked_input.read_masked("ID: ")
        terminal.read.assert_not_called()

    def test_posix_restores_terminal_after_invalid_input(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        terminal.fileno.return_value = 7
        terminal.read.side_effect = ["a", "\x16"]
        stderr = Mock()
        stderr.isatty.return_value = True
        termios = types.SimpleNamespace(tcgetattr=Mock(return_value="old"), tcsetattr=Mock(), TCSADRAIN=1)
        tty = types.SimpleNamespace(setcbreak=Mock())
        with patch.object(sys, "stdin", terminal), patch.object(sys, "__stdin__", terminal), \
             patch.object(sys, "stderr", stderr), patch.object(masked_input.os, "name", "posix"), \
             patch.dict(sys.modules, {"termios": termios, "tty": tty}):
            with self.assertRaises(masked_input.PrivateInputError):
                masked_input.read_masked("ID: ")
        termios.tcsetattr.assert_called_once_with(7, 1, "old")
        self.assertEqual("".join(call.args[0] for call in stderr.write.call_args_list), "ID: *\n")

if __name__ == "__main__":
    unittest.main()
