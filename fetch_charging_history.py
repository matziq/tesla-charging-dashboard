#!/usr/bin/env python3
"""Save one Tesla Fleet API charging-history response for private inspection."""

import http.client
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
URL = "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/dx/charging/history"
MAX_BYTES = 10 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_first_page(token):
    if not token or not token.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in token):
        raise ValueError("Tesla access token must be a valid bearer token")
    request = urllib.request.Request(
        URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"Tesla returned HTTP {response.status}; no response was saved")
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(f"Tesla returned HTTP {error.code}; no response was saved") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise ValueError("Network or TLS error while contacting Tesla; no response was saved") from None
    if len(body) > MAX_BYTES:
        raise ValueError("Tesla response exceeds 10 MiB; no response was saved")
    return body


def save_response(body):
    private_dir = ROOT / ".private"
    private_dir.mkdir(exist_ok=True)
    output = tempfile.NamedTemporaryFile(
        mode="wb", prefix="charging-history-", suffix=".bin",
        dir=private_dir, delete=False,
    )
    try:
        with output:
            output.write(body)
    except OSError:
        Path(output.name).unlink(missing_ok=True)
        raise
    return output.name


def main():
    try:
        body = fetch_first_page(os.environ.get("TESLA_FLEET_ACCESS_TOKEN"))
        print(f"Saved first response for private inspection: {save_response(body)}")
    except (OSError, ValueError) as error:
        raise SystemExit(f"Fetch failed: {error}") from None


if __name__ == "__main__":
    main()
