"""Load explicitly selected local Tesla application credentials."""

import stat
from pathlib import Path

from masked_input import contains_unsupported_characters


MAX_ENV_FILE = 16 * 1024


def load_local_credentials(filename):
    try:
        path = Path(filename)
        is_symlink = path.is_symlink()
        path = path.resolve(strict=True)
        mode = path.stat().st_mode
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Credential file could not be opened") from None
    if is_symlink:
        raise ValueError("Credential file must not be a symbolic link")
    if not stat.S_ISREG(mode):
        raise ValueError("Credential file must be a regular file")
    try:
        in_repository = any((parent / ".git").exists() for parent in path.parents)
    except OSError:
        raise ValueError("Credential file location could not be checked") from None
    if in_repository:
        raise ValueError("Credential file must be outside Git repositories")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_ENV_FILE + 1)
    except OSError:
        raise ValueError("Credential file could not be opened") from None
    if len(data) > MAX_ENV_FILE:
        raise ValueError("Credential file exceeds the size limit")
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeError:
        raise ValueError("Credential file must be UTF-8") from None
    fields = {}
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (not separator or key not in ("CLIENT_ID", "CLIENT_SECRET")
                or key in fields or not value or value != value.strip()
                or value[0] in ("'", '"') or contains_unsupported_characters(value)):
            raise ValueError("Credential file has invalid or duplicate entries")
        fields[key] = value
    if set(fields) != {"CLIENT_ID", "CLIENT_SECRET"}:
        raise ValueError("Credential file must contain CLIENT_ID and CLIENT_SECRET")
    return fields["CLIENT_ID"], fields["CLIENT_SECRET"]
