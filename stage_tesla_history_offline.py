#!/usr/bin/env python3
"""Use previously authorized Windows offline access to stage bounded history."""

import argparse
import re

import authorize_tesla as auth
import stage_tesla_history as staging
from fetch_charging_history import MAX_STAGED_PAGES
from tesla_token_store import TokenStore


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid command options; use --help for required acknowledgments.\n")


def safe_error(error):
    messages = {
        "A previous refresh has an uncertain outcome; manual recovery is required":
            "The saved refresh is pending; manual recovery is required.",
        "Offline credentials are unavailable; complete offline authorization first":
            "Offline credentials are unavailable; complete offline setup first.",
        "Another offline authorization or refresh is in progress":
            "Another offline authorization or staging run holds the lock.",
        "Tesla token response did not contain an access token":
            "The refresh response had no access token; no history request was sent.",
        "Offline token storage requires Windows":
            "Offline token storage requires Windows.",
    }
    message = str(error)
    if message in messages:
        return messages[message]
    match = re.fullmatch(
        r"Tesla token exchange returned HTTP ([1-5][0-9]{2})\. "
        r"Refresh outcome may be uncertain; do not retry automatically", message)
    if match:
        return f"Token refresh returned HTTP {match.group(1)}; its outcome may be uncertain."
    return "Offline token, storage or staging failed; check the documented recovery steps."


def run(store, cap):
    if type(cap) is not int or not 1 <= cap <= MAX_STAGED_PAGES:
        raise ValueError("Invalid history request limit")
    print(f"Offline staging: at most {cap} history GETs; failed requests may be billable.")
    print("Previously granted charging scope also permits commands; this tool sends none.")
    print("Responses are stored unencrypted locally; pagination and completeness are unverified.")
    return auth.with_refreshed_access(store, lambda token: staging.stage(token, cap))


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--max-pages", type=int, choices=range(1, MAX_STAGED_PAGES + 1),
                        required=True, help="maximum history GETs for this run (1-5)")
    parser.add_argument("--accept-offline-scope-and-billing", action="store_true",
                        help="acknowledge offline charging-command scope and potential billing")
    parser.add_argument("--accept-unencrypted-storage-and-unverified-pagination", action="store_true",
                        help="acknowledge private unencrypted storage and unverified pagination")
    args = parser.parse_args(argv)
    if not (args.accept_offline_scope_and_billing
            and args.accept_unencrypted_storage_and_unverified_pagination):
        parser.error("Both acknowledgments are required")
    try:
        return run(TokenStore(), args.max_pages)
    except (ValueError, OSError) as error:
        print(f"Offline staging stopped: {safe_error(error)}")
        print("No automatic retry; inspect token state before another attempt.")
        return 1
    except KeyboardInterrupt:
        print("Offline staging canceled; any saved pages remain private and completeness is unverified.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
