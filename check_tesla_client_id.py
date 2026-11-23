#!/usr/bin/env python3
"""Compare two privately entered client IDs without contacting Tesla."""

import getpass
import re
import unicodedata
import warnings


UUID_SHAPE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def has_control_or_format_characters(value):
    return any(unicodedata.category(char) in ("Cc", "Cf") for char in value)


def yes_no(value):
    return "yes" if value else "no"


def main():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            pasted = getpass.getpass("Paste the client ID (hidden): ")
            typed = getpass.getpass("Type the same client ID manually (hidden): ")
    except (getpass.GetPassWarning, EOFError, OSError):
        raise SystemExit("Check failed: a private interactive terminal is required") from None
    except KeyboardInterrupt:
        raise SystemExit("Check canceled") from None

    trimmed = pasted.strip()
    print(f"Entries match exactly: {yes_no(bool(pasted) and pasted == typed)}")
    print(f"First entry changed by the registration helper's trimming: {yes_no(pasted != trimmed)}")
    print(f"Either entry contains control or invisible format characters: {yes_no(has_control_or_format_characters(pasted) or has_control_or_format_characters(typed))}")
    print(f"First entry after trimming is UUID-shaped: {yes_no(bool(UUID_SHAPE.fullmatch(trimmed)))}")
    print("These offline checks do not confirm Tesla recognizes the client ID.")


if __name__ == "__main__":
    main()
