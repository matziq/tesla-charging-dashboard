#!/usr/bin/env python3
"""Interactively register the verified application domain with Tesla."""

import base64
import binascii
import hashlib
import hmac
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from authorize_tesla import AUDIENCE, TOKEN_URL
from fetch_charging_history import NoRedirect
from masked_input import PrivateInputError, read_masked


DOMAIN = "memconfigmgr.org"
KEY_URL = "https://memconfigmgr.org/.well-known/appspecific/com.tesla.3p.public-key.pem"
KEY_SHA256 = "023eeb7364a60478e92fd3f77c5a630df9eef1eaf4e9e14d8d35a1a77fc2c357"
REGISTER_URL = AUDIENCE + "/api/1/partner_accounts"
LOOKUP_URL = REGISTER_URL + "/public_key?" + urllib.parse.urlencode({"domain": DOMAIN})
MAX_RESPONSE = 1024 * 1024
MAX_OAUTH_ERROR = 16 * 1024
OAUTH_ERROR_CATEGORIES = {category: f"OAuth error: {category}" for category in (
    "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "invalid_scope", "invalid_client_id", "server_error",
    "client_not_found",
)}


def oauth_error_summary(error):
    try:
        body = error.read(MAX_OAUTH_ERROR + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return "OAuth error response: unreadable"
    if len(body) > MAX_OAUTH_ERROR:
        return "OAuth error response: exceeds diagnostic limit"
    if not body:
        return "OAuth error response: empty"
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return "OAuth error response: not parseable JSON"
    if not isinstance(payload, dict):
        return "OAuth error response: JSON value is not an object"
    if "error" not in payload:
        return "OAuth error response: JSON object has no error field"
    category = payload["error"]
    if not isinstance(category, str):
        return "OAuth error response: JSON error field is not a string"
    return OAUTH_ERROR_CATEGORIES.get(category, "OAuth error response: JSON error category unrecognized")


def request_bytes(request, label, allow_not_found=False, oauth_error=False):
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"{label} returned HTTP {response.status}")
            body = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        try:
            if allow_not_found and error.code == 404:
                return None
            summary = oauth_error_summary(error) if oauth_error else None
            suffix = f" ({summary})" if summary else ""
            raise ValueError(f"{label} returned HTTP {error.code}{suffix}") from None
        finally:
            try:
                error.close()
            except OSError:
                raise ValueError(f"{label} error response could not be closed") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise ValueError(f"Network or TLS error during {label.lower()}") from None
    if len(body) > MAX_RESPONSE:
        raise ValueError(f"{label} response exceeded 1 MiB")
    return body


def key_matches(pem):
    try:
        if isinstance(pem, bytes):
            pem = pem.decode("ascii")
        if not isinstance(pem, str):
            return False
        lines = pem.strip().splitlines()
        if len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
            return False
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (UnicodeError, ValueError, binascii.Error):
        return False
    return hmac.compare_digest(hashlib.sha256(der).hexdigest(), KEY_SHA256)


def verify_hosted_key():
    response = request_bytes(urllib.request.Request(KEY_URL, method="GET"), "Hosted key")
    if not key_matches(response):
        raise ValueError("Hosted public key does not match the verified Tesla application key")


def read_json(data, label):
    try:
        result = json.loads(data)
    except (UnicodeError, ValueError):
        raise ValueError(f"{label} response was not valid JSON") from None
    if not isinstance(result, dict):
        raise ValueError(f"{label} response had an unexpected format")
    return result


def partner_token(client_id, client_secret):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": AUDIENCE,
    }).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    payload = read_json(request_bytes(request, "Partner token", oauth_error=True), "Partner token")
    token = payload.get("access_token")
    if (not isinstance(token, str) or not token or not token.isascii()
            or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in token)):
        raise ValueError("Partner token response did not contain a valid access token")
    return token


def registered_key(token):
    request = urllib.request.Request(
        LOOKUP_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    body = request_bytes(request, "Partner key lookup", allow_not_found=True)
    if body is None:
        return None
    result = read_json(body, "Partner key lookup").get("response")
    if not isinstance(result, dict) or not isinstance(result.get("public_key"), str):
        raise ValueError("Partner key lookup response did not contain a recognizable public key")
    return result["public_key"]


def register(token):
    request = urllib.request.Request(
        REGISTER_URL, data=json.dumps({"domain": DOMAIN}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        request_bytes(request, "Partner registration")
    except ValueError as error:
        raise ValueError(f"{error}; registration outcome is unconfirmed, check Tesla before retrying") from None
    try:
        key = registered_key(token)
    except ValueError:
        raise ValueError("Registration POST returned HTTP 200, but verification failed; check Tesla before retrying") from None
    if key is None or not key_matches(key):
        raise ValueError("Registration POST returned HTTP 200, but the registered key could not be verified; check Tesla before retrying")


def main():
    try:
        print("Tesla partner helper: diagnostic format v3")
        verify_hosted_key()
        print("Hosted key verified. Confirm the Tesla application allows https://memconfigmgr.org.")
        if input("Type CONTINUE to enter credentials, or press Enter to cancel: ").strip() != "CONTINUE":
            raise ValueError("Canceled; no authenticated request was sent")
        client_id = read_masked("Tesla application client ID (masked): ").strip()
        client_secret = read_masked("Tesla application client secret (masked): ")
        if not client_id or not client_secret:
            raise ValueError("Client ID and client secret are required")
        token = partner_token(client_id, client_secret)
        key = registered_key(token)
        if key is not None:
            if not key_matches(key):
                raise ValueError("Tesla has a different registered key; no registration was attempted")
            print("Tesla already has the expected public key registered in North America.")
            return
        print("Tesla returned HTTP 404 for the key lookup; this does not prove registration is absent.")
        print("The registration POST can change the domain's registered key.")
        if input("Type REGISTER to proceed, or press Enter to cancel: ").strip() != "REGISTER":
            raise ValueError("Canceled; no registration POST was sent")
        verify_hosted_key()
        key = registered_key(token)
        if key is not None:
            if not key_matches(key):
                raise ValueError("Tesla now has a different registered key; no registration was attempted")
            print("Tesla already has the expected public key registered in North America.")
            return
        register(token)
        print("Tesla's North American partner registration returned the expected public key.")
    except EOFError:
        raise SystemExit("Registration failed: a private interactive terminal is required") from None
    except PrivateInputError as error:
        raise SystemExit(f"Registration failed: {error}") from None
    except (ValueError, OSError) as error:
        raise SystemExit(f"Registration failed: {error}") from None
    except KeyboardInterrupt:
        raise SystemExit("Registration canceled") from None


if __name__ == "__main__":
    main()
