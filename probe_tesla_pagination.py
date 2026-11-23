#!/usr/bin/env python3
"""Interactively compare two Tesla history pages; do not infer completeness."""

import argparse
import os
import secrets
import sys
import tempfile
from pathlib import Path

import authorize_tesla as auth
from compare_tesla_history_pages import compare_pages, read_page
from fetch_charging_history import ROOT, fetch_probe_page
from masked_input import PrivateInputError


def require_terminal():
    if sys.stdin is not sys.__stdin__ or not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PrivateInputError("a private interactive terminal is required")


def private_directory(prefix="pagination-probe-"):
    directory = ROOT / ".private"
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if (directory.is_symlink() or getattr(directory, "is_junction", lambda: False)() or not directory.is_dir()
                or directory.resolve().parent != ROOT.resolve()):
            raise ValueError("The private output directory must be a local non-link directory")
        return Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    except OSError:
        raise ValueError("Could not create a private response directory") from None


def save_page(directory, page, body):
    path = directory / f"page-{page}.bin"
    created = False
    try:
        with path.open("xb") as output:
            created = True
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise ValueError("Could not save a response; a partial file may remain in the private directory") from None
        raise ValueError("Could not save a response; check local storage before retrying") from None
    return path


def run(env_file=None):
    require_terminal()
    print("This experiment uses the Tesla charging-management scope, which also permits charging commands.")
    print("It makes up to two read-only history requests; Tesla may bill even failed requests.")
    print("Responses will be saved unencrypted under .private; protect access, backups and sync.")
    print("The proposed page parameters and completeness are unverified. No registration is attempted.")
    if input("Type CONTINUE to begin authorization, or press Enter to cancel: ").strip() != "CONTINUE":
        raise ValueError("Canceled before authorization")
    client_id, client_secret = auth.application_credentials(env_file)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    code = auth.wait_for_code(client_id, state, verifier)
    token = auth.exchange_code(client_id, client_secret, code, verifier)
    print("Next: up to two potentially billable GETs; page parameters and completeness are unverified.")
    print("The scope also allows charging commands; responses are saved unencrypted locally.")
    if input("Type FETCH to make the two requests, or press Enter to cancel: ").strip() != "FETCH":
        raise ValueError("Canceled; no history requests were sent")
    directory = private_directory()
    print(f"Private response directory: {directory}")
    pages = []
    for page in (1, 2):
        try:
            body = fetch_probe_page(token, page)
            path = save_page(directory, page, body)
            pages.append(read_page(path))
        except ValueError as error:
            raise ValueError(f"Page {page} failed: {error}. No automatic retry; any saved page remains private.") from None
    first, second = pages
    totals, overlap = compare_pages(first, second)
    print(f"Page record counts: {len(first[1])} and {len(second[1])}. Reported totals: {totals}.")
    print(f"Session-ID overlap: {overlap}.")
    print("These results do not prove pagination semantics, complete history, or registration.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", metavar="PATH", help="read application credentials from a local file")
    args = parser.parse_args(argv)
    try:
        run(args.env_file)
    except (ValueError, PrivateInputError) as error:
        raise SystemExit(f"Pagination probe stopped: {error}") from None
    except EOFError:
        raise SystemExit("Pagination probe stopped: a private interactive terminal is required") from None
    except KeyboardInterrupt:
        raise SystemExit("Pagination probe canceled; any saved page remains private") from None


if __name__ == "__main__":
    main()
