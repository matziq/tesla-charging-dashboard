import base64
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
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
        self.assertEqual(fields["client_id"], ["id-secret"])
        self.assertEqual(fields["client_secret"], ["client-secret"])
        self.assertEqual(token, "token-secret")

    def test_local_credentials_are_read_literally_without_output(self):
        secret = "$(synthetic-command);secret"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            original = f"# local only\nCLIENT_ID=synthetic-id\nCLIENT_SECRET={secret}\n".encode()
            path.write_bytes(original)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = partner.load_local_credentials(path)
            self.assertEqual(result, ("synthetic-id", secret))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(path.read_bytes(), original)

    def test_local_credentials_reject_invalid_content_without_disclosure(self):
        cases = (
            b"CLIENT_ID=id\nCLIENT_SECRET=secret\nCLIENT_ID=other\n",
            b"CLIENT_ID=id\nCLIENT_SECRET=secret\nOTHER=private-detail\n",
            b"CLIENT_ID=id\nCLIENT_SECRET='private-detail'\n",
            b"CLIENT_ID=id\nCLIENT_SECRET= secret\n",
            b"CLIENT_ID=id\nCLIENT_SECRET=secret\x16\n",
            b"CLIENT_ID=id\n",
            b"CLIENT_ID=id\nCLIENT_SECRET=\xff\n",
            b"x" * (partner.MAX_ENV_FILE + 1),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for content in cases:
                with self.subTest(content_type=cases.index(content)):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError) as failure:
                        partner.load_local_credentials(path)
                    self.assertNotIn("private-detail", str(failure.exception))
                    self.assertNotIn(str(path), str(failure.exception))

    def test_local_credentials_reject_git_worktrees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            path = root / ".env"
            path.write_text("CLIENT_ID=id\nCLIENT_SECRET=secret\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside Git repositories"):
                partner.load_local_credentials(path)

    def test_local_credentials_reject_symbolic_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("CLIENT_ID=id\nCLIENT_SECRET=secret\n", encoding="utf-8")
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(ValueError, "must not be a symbolic link"):
                    partner.load_local_credentials(path)

    def test_file_credentials_require_confirmation_before_reading(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key"), patch("builtins.input", return_value=""), \
             patch.object(partner, "load_local_credentials") as load, \
             patch.object(partner, "partner_token") as token, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "Canceled"):
                partner.main("unused-file")
        load.assert_not_called()
        token.assert_not_called()

    def test_file_credentials_are_used_only_in_memory_after_confirmation(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("CLIENT_ID=synthetic-id\nCLIENT_SECRET=synthetic-secret\n", encoding="utf-8")
            with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
                 patch.object(partner.sys, "stderr", stderr), \
                 patch.object(partner, "verify_hosted_key"), patch("builtins.input", return_value="CONTINUE"), \
                 patch.object(partner, "partner_token", return_value="fake-token") as token, \
                 patch.object(partner, "registered_key", return_value=PEM), \
                 patch.object(partner, "KEY_SHA256", FINGERPRINT), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                partner.main(str(path))
            token.assert_called_once_with("synthetic-id", "synthetic-secret")
            for value in ("synthetic-id", "synthetic-secret", "fake-token"):
                self.assertNotIn(value, output.getvalue())

    def test_file_credentials_reject_noninteractive_input(self):
        terminal = Mock()
        terminal.isatty.return_value = False
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner, "verify_hosted_key") as verify, \
             patch.object(partner, "load_local_credentials") as load:
            with self.assertRaisesRegex(SystemExit, "private interactive terminal"):
                partner.main("unused-file")
        verify.assert_not_called()
        load.assert_not_called()

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
        self.assertEqual(lookup.get_header("Content-type"), "application/json")
        self.assertEqual(lookup.get_header("Authorization"), "Bearer token-secret")

    def test_post_success_without_verified_lookup_is_uncertain(self):
        with patch.object(partner, "request_bytes", side_effect=[b'{}', None]):
            with self.assertRaisesRegex(ValueError, "could not be verified"):
                partner.register("token")
        with patch.object(partner, "request_bytes", side_effect=[b'{}', ValueError("private detail")]):
            with self.assertRaises(ValueError) as failure:
                partner.register("token")
            self.assertIn("verification failed", str(failure.exception))
            self.assertNotIn("private detail", str(failure.exception))

    def test_post_success_reports_forbidden_verification_without_body(self):
        forbidden = partner.PartnerLookupForbidden("private response detail")
        with patch.object(partner, "request_bytes", return_value=b'{}') as request, \
             patch.object(partner, "registered_key", side_effect=forbidden):
            with self.assertRaises(ValueError) as failure:
                partner.register("token-secret")
        self.assertEqual(str(failure.exception),
                         "Registration POST returned HTTP 200, but verification lookup returned HTTP 403; "
                         "the outcome is unconfirmed, check Tesla before retrying")
        self.assertEqual(request.call_count, 1)

    def test_failed_registration_request_reports_uncertain_outcome(self):
        with patch.object(partner, "request_bytes", side_effect=ValueError("Network error")) as request:
            with self.assertRaisesRegex(ValueError, "outcome is unconfirmed"):
                partner.register("token")
        self.assertEqual(request.call_count, 1)

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

    def test_client_not_found_category_does_not_expose_other_response_fields(self):
        body = b'{"error":"client_not_found","error_description":"secret-detail","client_secret":"secret-value"}'
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            partner.TOKEN_URL, 400, "reason-secret", {}, io.BytesIO(body)
        )
        with patch.object(partner.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError) as failure:
                partner.partner_token("id-secret", "secret-value")
        self.assertEqual(str(failure.exception), "Partner token returned HTTP 400 (OAuth error: client_not_found)")

    def test_unrecognized_malformed_and_oversized_token_errors_are_sanitized(self):
        cases = (
            (b'', "empty"),
            (b'{"error":"unknown-secret","error_description":"description-secret"}', "JSON error category unrecognized"),
            (b'{"error":"client_not_found secret-value"}', "JSON error category unrecognized"),
            (b'{"error":["invalid_client"],"access_token":"token-secret"}', "JSON error field is not a string"),
            (b'{"access_token":"token-secret"}', "JSON object has no error field"),
            (b'["client-secret"]', "JSON value is not an object"),
            (b'{"error":"invalid_client","error_description":"secret"', "not parseable JSON"),
            (b'\xffsecret', "not parseable JSON"),
            (b'[' * 2000 + b'0' + b']' * 2000, ("not parseable JSON", "JSON value is not an object")),
            (b'{"error":"invalid_client","padding":"' + b"x" * partner.MAX_OAUTH_ERROR + b'"}', "exceeds diagnostic limit"),
        )
        for body, label in cases:
            with self.subTest(body=body[:40]):
                opener = Mock()
                opener.open.side_effect = urllib.error.HTTPError(
                    partner.TOKEN_URL, 400, "reason-secret", {}, io.BytesIO(body)
                )
                with patch.object(partner.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(ValueError) as failure:
                        partner.partner_token("id-secret", "client-secret")
                labels = label if isinstance(label, tuple) else (label,)
                self.assertIn(str(failure.exception), [
                    f"Partner token returned HTTP 400 (OAuth error response: {value})"
                    for value in labels
                ])

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
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner.urllib.request, "build_opener", return_value=opener), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                partner.main()
        self.assertEqual(str(failure.exception), "Registration failed: Partner token returned HTTP 400 (OAuth error: invalid_client)")
        register.assert_not_called()

    def test_control_input_stops_before_token_request(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner, "read_masked", side_effect=partner.PrivateInputError("Unsupported control character")), \
             patch.object(partner, "partner_token") as token, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "Unsupported control character"):
                partner.main()
        token.assert_not_called()

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

    def test_unreadable_error_body_uses_fixed_label(self):
        stream = Mock()
        stream.read.side_effect = OSError("read-secret")
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.TOKEN_URL, 400, "", {}, stream)
        with patch.object(partner.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError) as failure:
                partner.partner_token("id", "secret")
        self.assertEqual(str(failure.exception), "Partner token returned HTTP 400 (OAuth error response: unreadable)")

    def test_launch_displays_diagnostic_version(self):
        output = io.StringIO()
        with patch.object(partner, "verify_hosted_key", side_effect=ValueError("test")), \
             contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):
                partner.main()
        self.assertIn("Tesla partner helper: diagnostic format v3", output.getvalue())

    def test_existing_different_key_never_registers(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", return_value="different key"), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "different registered key"):
                partner.main()
            register.assert_not_called()

    def test_lookup_403_never_registers_or_discloses_body(self):
        stream = Mock(wraps=io.BytesIO(b'{"error":"private-detail","token":"token-secret"}'))
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.LOOKUP_URL, 403, "", {}, stream)
        with patch.object(partner, "verify_hosted_key"), \
            patch("builtins.input", return_value="CONTINUE"), \
            patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
            patch.object(partner, "partner_token", return_value="token-secret"), \
            patch.object(partner.urllib.request, "build_opener", return_value=opener), \
            patch.object(partner, "register") as register, \
            contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as failure:
               partner.main()
        self.assertEqual(str(failure.exception), "Registration failed: Partner key lookup returned HTTP 403")
        stream.read.assert_not_called()
        register.assert_not_called()
        self.assertNotIn("token-secret", output.getvalue())

    def test_read_only_verification_reports_match_without_registration(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key"), patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", return_value=PEM) as lookup, \
             patch.object(partner, "KEY_SHA256", FINGERPRINT), \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()) as output:
            partner.main(verify_only=True)
        lookup.assert_called_once_with("token-secret")
        register.assert_not_called()
        self.assertIn("Tesla returned the expected public key", output.getvalue())
        for secret in ("id-secret", "client-secret", "token-secret", PEM):
            self.assertNotIn(secret, output.getvalue())

    def test_read_only_verification_handles_unknown_or_wrong_key_safely(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        cases = (
            (None, "HTTP 404; registration remains unconfirmed"),
            ("different-secret-key", "registered key does not match"),
            (partner.PartnerLookupForbidden("Partner key lookup returned HTTP 403"), "HTTP 403"),
            (ValueError("Partner key lookup response did not contain a recognizable public key"),
             "did not contain a recognizable public key"),
        )
        for result, expected in cases:
            with self.subTest(expected=expected), \
                 patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
                 patch.object(partner.sys, "stderr", stderr), \
                 patch.object(partner, "verify_hosted_key"), patch("builtins.input", return_value="CONTINUE"), \
                 patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
                 patch.object(partner, "partner_token", return_value="token-secret"), \
                 patch.object(partner, "registered_key", side_effect=[result]), \
                 patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as failure:
                    partner.main(verify_only=True)
                self.assertIn(expected, str(failure.exception))
                for secret in ("id-secret", "client-secret", "token-secret", "different-secret-key"):
                    self.assertNotIn(secret, str(failure.exception))
                    self.assertNotIn(secret, output.getvalue())
                register.assert_not_called()

    def test_read_only_verification_requires_confirmation_and_cannot_combine_modes(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key") as verify, patch("builtins.input", return_value=""), \
             patch.object(partner, "partner_token") as token, \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "Canceled"):
                partner.main(verify_only=True)
            verify.reset_mock()
            with self.assertRaisesRegex(SystemExit, "cannot be combined"):
                partner.main(first_registration=True, verify_only=True)
        verify.assert_not_called()
        token.assert_not_called()
        register.assert_not_called()

    def test_read_only_verification_rejects_noninteractive_execution(self):
        terminal = Mock()
        terminal.isatty.return_value = False
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner, "verify_hosted_key") as verify, \
             patch.object(partner, "partner_token") as token:
            with self.assertRaisesRegex(SystemExit, "private interactive terminal"):
                partner.main(verify_only=True)
        verify.assert_not_called()
        token.assert_not_called()

    def test_read_only_lookup_403_does_not_read_sensitive_response(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        stream = Mock(wraps=io.BytesIO(b'{"error":"private-detail","token":"token-secret"}'))
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(partner.LOOKUP_URL, 403, "", {}, stream)
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key"), patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner.urllib.request, "build_opener", return_value=opener), \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "Partner key lookup returned HTTP 403"):
                partner.main(verify_only=True)
        stream.read.assert_not_called()
        register.assert_not_called()

    def test_explicit_first_registration_requires_risk_attestation(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        for answer, should_register in (("", False), ("REGISTER", False),
                                        ("I CONTROL THE DOMAIN AND ACCEPT KEY REPLACEMENT RISK", True)):
            with self.subTest(should_register=should_register, answer=answer):
                forbidden = partner.PartnerLookupForbidden("Partner key lookup returned HTTP 403")
                with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
                     patch.object(partner.sys, "stderr", stderr), \
                     patch.object(partner, "verify_hosted_key") as verify, \
                     patch("builtins.input", side_effect=["CONTINUE", answer]), \
                     patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
                     patch.object(partner, "partner_token", return_value="token-secret"), \
                     patch.object(partner, "registered_key", side_effect=[forbidden, forbidden]) as lookup, \
                     patch.object(partner, "register") as register, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    if should_register:
                        partner.main(first_registration=True)
                    else:
                        with self.assertRaisesRegex(SystemExit, "no registration POST"):
                            partner.main(first_registration=True)
                self.assertEqual(verify.call_count, 2)
                self.assertEqual(lookup.call_count, 2)
                self.assertEqual(register.call_count, int(should_register))
                self.assertIn("may replace a different key and disrupt other integrations", output.getvalue())
                for secret in ("id-secret", "client-secret", "token-secret"):
                    self.assertNotIn(secret, output.getvalue())

    def test_first_registration_rejects_newly_observed_different_key(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        forbidden = partner.PartnerLookupForbidden("Partner key lookup returned HTTP 403")
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE") as prompt, \
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
             patch.object(partner, "partner_token", return_value="token-secret"), \
             patch.object(partner, "registered_key", side_effect=[forbidden, "different key"]), \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "different registered key"):
                partner.main(first_registration=True)
        register.assert_not_called()
        prompt.assert_called_once()

    def test_attestation_is_last_step_before_registration(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        events = []
        def verify():
            events.append("verify")
        def lookup(token):
            events.append("lookup")
            raise partner.PartnerLookupForbidden("Partner key lookup returned HTTP 403")
        def prompt(message):
            if not events or events[-1] != "lookup":
                return "CONTINUE"
            events.append("attest")
            return "I CONTROL THE DOMAIN AND ACCEPT KEY REPLACEMENT RISK"
        def register(token):
            events.append("post")
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key", side_effect=verify), \
             patch("builtins.input", side_effect=prompt), \
             patch.object(partner, "read_masked", side_effect=["id", "secret"]), \
             patch.object(partner, "partner_token", return_value="token"), \
             patch.object(partner, "registered_key", side_effect=lookup), \
             patch.object(partner, "register", side_effect=register), \
             contextlib.redirect_stdout(io.StringIO()):
            partner.main(first_registration=True)
        self.assertEqual(events, ["verify", "lookup", "verify", "lookup", "attest", "post"])

    def test_first_registration_does_not_ignore_other_lookup_errors(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", return_value="CONTINUE"), \
             patch.object(partner, "read_masked", side_effect=["id", "secret"]), \
             patch.object(partner, "partner_token", return_value="token"), \
             patch.object(partner, "registered_key", side_effect=ValueError("Partner key lookup returned HTTP 500")), \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "HTTP 500"):
                partner.main(first_registration=True)
        register.assert_not_called()

    def test_first_registration_stops_if_recheck_fails(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        forbidden = partner.PartnerLookupForbidden("Partner key lookup returned HTTP 403")
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner.sys, "stderr", stderr), \
             patch.object(partner, "verify_hosted_key", side_effect=[None, ValueError("key mismatch")]), \
             patch("builtins.input", return_value="CONTINUE") as prompt, \
             patch.object(partner, "read_masked", side_effect=["id", "secret"]), \
             patch.object(partner, "partner_token", return_value="token"), \
             patch.object(partner, "registered_key", side_effect=forbidden), \
             patch.object(partner, "register") as register, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "key mismatch"):
                partner.main(first_registration=True)
        register.assert_not_called()
        prompt.assert_called_once()

    def test_first_registration_requires_interactive_terminal(self):
        terminal = Mock()
        terminal.isatty.return_value = False
        with patch.object(partner.sys, "stdin", terminal), patch.object(partner.sys, "__stdin__", terminal), \
             patch.object(partner, "verify_hosted_key") as verify:
            with self.assertRaisesRegex(SystemExit, "private interactive terminal"):
                partner.main(first_registration=True)
        verify.assert_not_called()

    def test_lookup_404_needs_second_explicit_confirmation(self):
        with patch.object(partner, "verify_hosted_key"), \
             patch("builtins.input", side_effect=["CONTINUE", ""]), \
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
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
             patch.object(partner, "read_masked", side_effect=["id-secret", "client-secret"]), \
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
             patch.object(partner, "read_masked", side_effect=["id", "secret"]), \
             patch.object(partner, "partner_token", return_value="token"), \
             patch.object(partner, "registered_key", side_effect=[None, "different key"]), \
             patch.object(partner, "register") as register, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "different registered key"):
                partner.main()
            register.assert_not_called()

    def test_unverified_hosted_key_stops_before_credentials(self):
        with patch.object(partner, "verify_hosted_key", side_effect=ValueError("key mismatch")), \
             patch.object(partner, "read_masked") as prompt, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "key mismatch"):
                partner.main()
            prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
