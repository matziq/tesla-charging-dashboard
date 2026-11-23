"""Read private interactive input with masked character feedback."""

import os
import sys
import unicodedata


class PrivateInputError(Exception):
    pass


def _contains_unsupported_characters(value):
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)


def _read_characters(prompt, read, write):
    chars = []
    write(prompt)
    try:
        while True:
            char = read()
            if not char:
                raise EOFError
            if char in ("\r", "\n"):
                return "".join(chars)
            if char == "\x03":
                raise KeyboardInterrupt
            if char in ("\b", "\x7f"):
                if chars:
                    chars.pop()
                    write("\b \b")
            elif _contains_unsupported_characters(char):
                raise PrivateInputError(
                    "Unsupported control or invisible character; use a supported terminal paste shortcut or type manually"
                )
            else:
                chars.append(char)
                write("*")
    finally:
        write("\n")


def read_masked(prompt):
    if sys.stdin is not sys.__stdin__ or not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PrivateInputError("a private interactive terminal is required")

    if os.name == "nt":
        import msvcrt

        def write(text):
            if text == "\n":
                text = "\r\n"
            for char in text:
                msvcrt.putwch(char)

        def read():
            char = msvcrt.getwch()
            if char == "\xe0":
                raise PrivateInputError("Unsupported special key or character")
            return char

        return _read_characters(prompt, read, write)

    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)

        def write(text):
            sys.stderr.write(text)
            sys.stderr.flush()

        return _read_characters(prompt, lambda: sys.stdin.read(1), write)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
