#!/usr/bin/env python3
"""Authorize locally with Tesla and save one private history response."""

import base64
import getpass
import hashlib
import hmac
import http.client
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from fetch_charging_history import NoRedirect, fetch_first_page, save_response


REDIRECT_URI = "http://localhost:8765/callback"
AUTH_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
AUDIENCE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
CALLBACK_SECONDS = 300


def authorization_url(client_id, state, verifier):
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid vehicle_charging_cmds",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if (self.client_address[0] != "127.0.0.1"
                or self.headers.get_all("Host") != [self.server.expected_host]
                or parts.scheme or parts.netloc or parts.path != "/callback"
                or len(parts.query) > 8192):
            self.respond(400)
            return
        try:
            params = urllib.parse.parse_qs(parts.query, keep_blank_values=True, max_num_fields=16)
        except ValueError:
            self.respond(400)
            return
        states = params.get("state", [])
        if (len(states) != 1 or not states[0].isascii()
                or not hmac.compare_digest(states[0], self.server.expected_state)):
            self.respond(400)
            return
        codes, errors = params.get("code", []), params.get("error", [])
        if len(codes) == 1 and codes[0] and len(codes[0]) <= 4096 and not errors:
            self.server.result = ("code", codes[0])
        elif len(errors) == 1 and errors[0] and not codes:
            self.server.result = ("error", None)
        else:
            self.respond(400)
            return
        self.respond(200)

    def respond(self, status):
        body = (b"Authorization received. Return to your terminal."
                if status == 200 else b"Invalid callback.")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LoopbackServer(HTTPServer):
    def handle_error(self, request, client_address):
        print("Local callback connection failed; waiting for a valid callback.", file=sys.stderr)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address


def wait_for_code(client_id, state, verifier):
    try:
        with LoopbackServer(("127.0.0.1", 8765), CallbackHandler) as server:
            server.expected_host = "localhost:8765"
            server.expected_state = state
            server.result = None
            server.timeout = 1
            try:
                opened = webbrowser.open(authorization_url(client_id, state, verifier))
            except webbrowser.Error:
                opened = False
            if not opened:
                raise ValueError("Could not open a browser; check the default browser and retry")
            print("Complete sign-in and consent in your browser; waiting up to five minutes.")
            deadline = time.monotonic() + CALLBACK_SECONDS
            while server.result is None and time.monotonic() < deadline:
                server.handle_request()
            if server.result is None:
                raise ValueError("Authorization timed out; retry after checking the exact redirect URI")
            if server.result[0] == "error":
                raise ValueError("Tesla authorization was declined or failed; no token was saved")
            return server.result[1]
    except OSError:
        raise ValueError("Could not use the loopback listener on port 8765; check whether it is in use") from None


def exchange_code(client_id, client_secret, code, verifier):
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": verifier,
        "audience": AUDIENCE,
        "redirect_uri": REDIRECT_URI,
    }).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"Tesla token exchange returned HTTP {response.status}")
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(f"Tesla token exchange returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise ValueError("Network or TLS error during Tesla token exchange") from None
    if len(body) > 1024 * 1024:
        raise ValueError("Tesla token response exceeded 1 MiB")
    try:
        payload = json.loads(body)
    except (UnicodeError, ValueError):
        raise ValueError("Tesla token response was not valid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise ValueError("Tesla token response did not contain an access token")
    return payload["access_token"]


def main():
    try:
        client_id = input("Tesla application client ID: ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            client_secret = getpass.getpass("Tesla application client secret (hidden): ")
        if not client_id or not client_secret:
            raise ValueError("Client ID and client secret are required")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(32)
        code = wait_for_code(client_id, state, verifier)
        token = exchange_code(client_id, client_secret, code, verifier)
        body = fetch_first_page(token)
        print(f"Saved first response for private inspection: {save_response(body)}")
    except (getpass.GetPassWarning, EOFError):
        raise SystemExit("Authorization failed: a private interactive terminal is required") from None
    except (ValueError, OSError) as error:
        raise SystemExit(f"Authorization failed: {error}") from None
    except KeyboardInterrupt:
        raise SystemExit("Authorization canceled") from None


if __name__ == "__main__":
    main()
