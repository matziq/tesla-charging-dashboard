import base64
import contextlib
import hashlib
import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

import register_tesla_partner as partner


DER = b"synthetic-public-key"
PEM = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(DER).decode() + "\n-----END PUBLIC KEY-----\n"
FINGERPRINT = hashlib.sha256(DER).hexdigest()


class PartnerTests(unittest.TestCase):
    def test_key_validation_handles_formatting_and_rejects_mismatch(self):
        with patch.object(partner, "KEY_SHA256", FINGERPRINT):
            self.assertTrue(partner.key_matches(PEM.encode("ascii")))
            self.assertTrue(partner.key_matches(PEM.replace("\n", "\r\n")))
            self.assertFalse(partner.key_matches(PEM.replace("PUBLIC", "PRIVATE")))
            self.assertFalse(partner.key_matches("-----BEGIN PUBLIC KEY-----\nYWJj\n-----END PUBLIC KEY-----"))

    def test_partner_token_request_and_no_output(self):
        fake = Mock(return_value=b'{"access_token":"token-secret"}')
        with patch.object(partner, "request_bytes", fake):
            token = partner.partner_token("id-secret", "client-secret")
        request = fake.call_args.args[0]
        fields = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(request.full_url, partner.TOKEN_URL)
        self.assertEqual(fields["grant_type"], ["client_credentials"])
        self.assertEqual(fields["audience"], [partner.AUDIENCE])
        self.assertEqual(fields["client_secret"], ["client-secret"])
        self.assertEqual(token, "token-secret")

    def test_lookup_and_registration_verify_key(self):
        calls = Mock(side_effect=[b'{}', json.dumps({"response": {"public_key": PEM}}).encode()])
        with patch.object(partner, "KEY_SHA256", FINGERPRINT), \
             patch.object(partner, "request_bytes", calls):
            partner.register("token-secret")
        registration, lookup = [call.args[0] for call in calls.call_args_list]
        self.assertEqual(registration.full_url, partner.REGISTER_URL)
        self.assertEqual(registration.method, "POST")
        self.assertEqual(json.loads(registration.data), {"domain": partner.DOMAIN})
        self.assertEqual(registration.get_header("Authorization"), "Bearer token-secret")
        self.assertEqual(lookup.full_url, partner.LOOKUP_URL)
        self.assertEqual(lookup.method, "GET")

    def test_post_success_without_verified_lookup_is_uncertain(self):
        with patch.object(partner, "request_bytes", side_effect=[b'{}', None]):
            with self.assertRaisesRegex(ValueError, "could not be verified"):
                partner.register("token")
        with patch.object(partner, "request_bytes", side_effect=[b'{}', ValueError("private detail")]):
            with self.assertRaises(ValueError) as failure:
                partner.register("token")
            self.assertIn("verification failed", str(failure.exception))
            self.assertNotIn("private detail", str(failure.exception))

    def test_failed_registration_request_reports_uncertain_outcome(self):
        with patch.object(partner, "request_bytes", side_effect=ValueError("Network error")):
            with self.assertRaisesRegex(ValueError, "outcome is unconfirmed"):
                partner.register("token")

    def test_lookup_rejects_unexpected_or_invalid_response(self):
        for body in (b"not-json", b'{"response":{}}', b'{"response":"unknown"}'):
            with self.subTest(body=body), patch.object(partner, "request_bytes", return_value=body):
                with self.assertRaises(ValueError):
                    partner.registered_key("token")

    def test_http_errors_and_redirects_do_not_leak_body(self):
        for status in (301, 401, 403, 429):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError(partner.TOKEN_URL, status, "secret-body", {}, None)
            with patch.object(partner.urllib.request, "build_opener", return_value=opener) as build:
                with self.assertRaises(ValueError) as failure:
                    partner.request_bytes(urllib.request.Request(partner.TOKEN_URL), "Token")
                self.assertIsInstance(build.call_args.args[0], partner.NoRedirect)
                self.assertIn(f"HTTP {status}", str(failure.exception))
                self.assertNotIn("secret", str(failure.exception))

    def test_token_errors_report_only_allowlisted_categories(self):
        for category in partner.OAUTH_ERROR_CATEGORIES:
            with self.subTest(category=category):
                body = json.dumps({
                    "error": category,
                    "error_description": "description-secret",
                    "access_token": "token-secret",
                    "client_secret": "client-secret",
                }).encode()
                opener = Mock()
                opener.open.side_effect = urllib.error.HTTPError(
                    partner.TOKEN_URL, 400, "reason-secret", {}, io.BytesIO(body)
                )
                with patch.object(partner.urllib.request, "build_opener", return_value=opener), \
                     contextlib.redirect_stdout(io.StringIO()) as stdout:
                    with self.assertRaises(ValueError) as failure:
                        partner.partner_token("id-secret", "client-secret")
                self.assertEqual(str(failure.exception), f"Partner token returned HTTP 400 (OAuth error: {category})")
                self.assertEqual(stdout.getvalue(), "")
                for secret in ("description-secret", "token-secret", "client-secret", "reason-secret"):
                    self.assertNotIn(secret, str(failure.exception))

    def test_unrecognized_malformed_and_oversized_token_errors_are_sanitized(self):
        bodies = (
            b'{"error":"unknown-secret","error_description":"description-secret"}',
            b'{"error":["invalid_client"],"access_token":"token-secret"}',
            b'{"error":"invalid_client","error_description":"secret"',
            b'\xffsecret',
            b'[' * 2000 + b'0' + b']' * 2000,
            b'{"error":"invalid_client","padding":"' + b"x" * partner.MAX_OAUTH_ERROR + b'"}',
        )
        for body in bodies:
            with self.subTest(body=body[:40]):
                opener = Mock()
                opener.open.side_effect = urllib.error.HTTPError(
                    partner.TOKEN_URL, 400, "reason-secret", {}, io.BytesIO(body)
                )
                with patch.object(partner.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(ValueError) as failure:
                        partner.partner_token("id-secret", "client-secret")
                self.assertEqual(str(failure.exception), "Partner token returned HTTP 400")

    def test_non_token_http_errors_do_not_read_or_classify_response_body(self):
        stream = Mock(wraps=io.BytesIO(b'{"error":"invalid_client","error_description":"secret"}'))
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.REGISTER_URL, 400, "secret", {}, stream)
        with patch.object(partner.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError) as failure:
                partner.request_bytes(urllib.request.Request(partner.REGISTER_URL), "Partner registration")
        self.assertEqual(str(failure.exception), "Partner registration returned HTTP 400")
        stream.read.assert_not_called()

    def test_token_error_reads_are_bounded(self):
        stream = Mock(wraps=io.BytesIO(b'{"error":"invalid_client"}'))
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.TOKEN_URL, 400, "", {}, stream)
        with patch.object(partner.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                partner.partner_token("id", "secret")
        stream.read.assert_called_once_with(partner.MAX_OAUTH_ERROR + 1)

    def test_interactive_token_failure_shows_only_safe_category(self):
        body = b'{"error":"invalid_client","error_description":"secret-detail","access_token":"secret-token"}'
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.TOKEN_URL, 400, "", {}, io.BytesIO(body))
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner.getpass, "getpass", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner.urllib.request, "build_opener", return_value=opener), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                partner.main()
        self.assertEqual(str(failure.exception), "Registration failed: Partner token returned HTTP 400 (OAuth error: invalid_client)")
        register.assert_not_called()

    def test_error_response_io_failure_is_sanitized(self):
        stream = Mock()
        stream.read.side_effect = OSError("read-secret")
        stream.close.side_effect = OSError("close-secret")
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.TOKEN_URL, 400, "", {}, stream)
        with patch.object(partner.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError) as failure:
                partner.partner_token("id", "secret")
        self.assertEqual(str(failure.exception), "Partner token error response could not be closed")

    def test_existing_different_key_never_registers(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner.getpass, "getpass", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", return_value="different key"), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "different registered key"):
                partner.main()
            register.assert_not_called()

    def test_lookup_404_needs_second_explicit_confirmation(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", side_effect=["CONTINUE", ""]), \
             patch.object(partner.getpass, "getpass", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", return_value=None), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "no registration POST"):
                partner.main()
            register.assert_not_called()

    def test_confirmed_registration_rechecks_key_without_printing_secrets(self):
        output = io.StringIO()
        with patch.object(partner, "verify_hosted_key") as verify, \
             patch("builtins.input", side_effect=["CONTINUE", "REGISTER"]), \
             patch.object(partner.getpass, "getpass", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", return_value=None), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(output):
            partner.main()
        self.assertEqual(verify.call_count, 2)
        register.assert_called_once_with("token-secret")
        for secret in ("id-secret", "client-secret", "token-secret"):
            self.assertNotIn(secret, output.getvalue())

    def test_new_registration_between_lookups_is_not_overwritten(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", side_effect=["CONTINUE", "REGISTER"]), \
             patch.object(partner.getpass, "getpass", side_effect=["id", "secret"]), \
             patch.object(partner, "partner_token", return_value="token"), \
             patch.object(partner, "registered_key", side_effect=[None, "different key"]), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "different registered key"):
                partner.main()
            register.assert_not_called()

    def test_unverified_hosted_key_stops_before_credentials(self):
        with patch.object(partner, "verify_hosted_key", side_effect=ValueError("key mismatch")), \
             patch.object(partner.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(SystemExit, "key mismatch"):
                partner.main()
            prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
