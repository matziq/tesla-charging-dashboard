#!/usr/bin/env python3
"""Interactively stage a bounded set of unverified Tesla history pages."""

import argparse
import re
import secrets
import sys

import authorize_tesla as auth
from compare_tesla_history_pages import parse_page
from dairy_charging import parse_candidates
from fetch_charging_history import MAX_STAGED_PAGES, STAGED_PAGE_SIZE, fetch_staged_page
from masked_input import PrivateInputError
from probe_tesla_pagination import private_directory, require_terminal, save_page


def stopped(reason):
    print(f"Stopped: {reason}")
    print("Previously staged pages, if any, remain private for individual review.")
    print("History is incomplete or unverified; no automatic retry or import was performed.")
    return 2


def stage(token, cap):
    if type(cap) is not int or not 1 <= cap <= MAX_STAGED_PAGES:
        raise ValueError("Invalid history request limit")
    directory = private_directory(prefix="history-stage-")
    print(f"Private response directory: {directory}")
    seen = set()
    expected_total = None
    for page_number in range(1, cap + 1):
        try:
            body = fetch_staged_page(token, page_number)
        except ValueError as error:
            match = re.fullmatch(r"Tesla returned HTTP ([1-5][0-9]{2}); no response was saved", str(error))
            reason = f"Tesla returned HTTP {match.group(1)}." if match else "History request or response failed."
            return stopped(reason)
        try:
            total, identifiers = parse_page(body)
            parse_candidates(body)
        except ValueError as error:
            if str(error) == "Page contains duplicate session identifiers":
                return stopped("A page contains repeated session identifiers; it was not staged.")
            return stopped("A page was malformed or cannot be reviewed safely; it was not staged.")
        if len(identifiers) > STAGED_PAGE_SIZE:
            return stopped("A page exceeded the requested size; it was not staged.")
        if expected_total is not None and total != expected_total:
            return stopped("Reported totals changed; the inconsistent page was not staged.")
        if seen.intersection(identifiers):
            return stopped("Session identifiers repeated across pages; the overlapping page was not staged.")
        new_count = len(seen) + len(identifiers)
        if new_count > total:
            return stopped("The accumulated rows exceeded the reported total; the page was not staged.")
        if len(identifiers) < STAGED_PAGE_SIZE and new_count < total:
            return stopped("A short or empty page appeared before the reported total; it was not staged.")
        try:
            save_page(directory, page_number, body)
        except ValueError:
            return stopped("A page could not be saved; a partial file may remain in the private directory.")
        expected_total = total
        seen.update(identifiers)
        print(f"Page {page_number} staged privately.")
        if len(seen) == total:
            print("Staged row count matches the reported total, but a stable snapshot and complete history are unverified.")
            print("Nothing was imported; review the saved pages manually in the dashboard.")
            return 0
    return stopped("The approved request cap was reached before the reported total was matched.")


def run(env_file=None):
    require_terminal()
    choice = input(f"Maximum history GETs (1-{MAX_STAGED_PAGES}); press Enter to cancel: ").strip()
    if not choice:
        raise ValueError("Canceled before authorization; no history requests were sent")
    if choice not in tuple(str(i) for i in range(1, MAX_STAGED_PAGES + 1)):
        raise ValueError("Choose a whole number within the displayed request limit")
    cap = int(choice)
    print("Tesla's charging-management scope also permits charging commands; this tool sends none.")
    print(f"This run makes at most {cap} read-only history GETs. Tesla may bill failed requests.")
    print("Responses will be saved unencrypted under .private; protect access, backups and sync.")
    print("Page parameters and completeness are unverified; no registration or offline access is attempted.")
    if input("Type CONTINUE to begin authorization, or press Enter to cancel: ").strip() != "CONTINUE":
        raise ValueError("Canceled before authorization; no history requests were sent")
    client_id, client_secret = auth.application_credentials(env_file)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    code = auth.wait_for_code(client_id, state, verifier, offline=False)
    token = auth.exchange_code(client_id, client_secret, code, verifier, offline=False)
    print(f"Next: at most {cap} potentially billable history GETs, with no automatic retries.")
    print("The approved scope permits charging commands; responses are saved unencrypted locally.")
    print("Pagination and completeness remain unverified.")
    if input("Type FETCH to make the requests, or press Enter to cancel: ").strip() != "FETCH":
        raise ValueError("Canceled; no history requests were sent")
    return stage(token, cap)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", metavar="PATH", help="read application credentials from a local file")
    args = parser.parse_args(argv)
    try:
        return run(args.env_file)
    except (ValueError, PrivateInputError) as error:
        print(f"History staging stopped: {error}")
    except EOFError:
        print("History staging stopped: a private interactive terminal is required.")
    except KeyboardInterrupt:
        print("History staging canceled; any saved pages remain private and completeness is unverified.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
