import base64
import contextlib
import hashlib
import http.client
import io
import json
import os
import threading
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

import authorize_tesla as auth


class TokenResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, size):
        return self.body[:size]


class AuthorizationTests(unittest.TestCase):
    def test_authorization_url_has_exact_redirect_and_s256(self):
        verifier = "a" * 43
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(
            auth.authorization_url("client", "state-value", verifier)
        ).query)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["redirect_uri"], [auth.REDIRECT_URI])
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["openid vehicle_charging_cmds"])
        self.assertNotIn("offline_access", query["scope"][0])

    def test_loopback_rejects_bad_state_host_and_duplicate_code(self):
        with auth.LoopbackServer(("127.0.0.1", 0), auth.CallbackHandler) as server:
            server.expected_host = "localhost:8765"
            server.expected_state = "state-value"
            server.result = None

            def request(path, host="localhost:8765"):
                thread = threading.Thread(target=server.handle_request)
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("GET", path, headers={"Host": host})
                    response = connection.getresponse()
                    result = response.status, response.read()
                    connection.close()
                    return result
                finally:
                    thread.join(timeout=3)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(request("/callback?state=wrong&code=secret")[0], 400)
                self.assertEqual(request("/callback?state=state-value&code=secret", "evil.example")[0], 400)
                self.assertEqual(request("/callback?state=state-value&code=a&code=b")[0], 400)
                self.assertIsNone(server.result)
                status, body = request("/callback?state=state-value&code=secret")
            self.assertEqual(status, 200)
            self.assertEqual(server.result, ("code", "secret"))
            self.assertNotIn(b"secret", body)
            self.assertNotIn("secret", stderr.getvalue())

    def test_callback_denial_is_generic(self):
        with auth.LoopbackServer(("127.0.0.1", 0), auth.CallbackHandler) as server:
            server.expected_host = "localhost:8765"
            server.expected_state = "state-value"
            server.result = None
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/callback?state=state-value&error=access_denied", headers={"Host": "localhost:8765"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            thread.join(timeout=3)
            self.assertEqual(server.result, ("error", None))

    def test_wait_for_code_starts_listener_before_opening_browser(self):
        requests = []
        threads = []
        servers = []

        class TestServer(auth.LoopbackServer):
            def __init__(self, address, handler):
                self.requested_address = address
                super().__init__(("127.0.0.1", 0), handler)
                servers.append(self)

        def open_browser(url):
            requests.append(url)
            def callback():
                connection = http.client.HTTPConnection("127.0.0.1", servers[0].server_port, timeout=3)
                connection.request("GET", "/callback?state=state-value&code=secret",
                                   headers={"Host": "localhost:8765"})
                response = connection.getresponse()
                response.read()
                connection.close()
            thread = threading.Thread(target=callback)
            threads.append(thread)
            thread.start()
            return True

        with patch.object(auth, "LoopbackServer", TestServer), \
             patch.object(auth.webbrowser, "open", side_effect=open_browser), \
             contextlib.redirect_stdout(io.StringIO()):
            code = auth.wait_for_code("client", "state-value", "v" * 43)
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(code, "secret")
        self.assertEqual(servers[0].requested_address, ("127.0.0.1", 8765))
        self.assertEqual(len(requests), 1)

    def test_token_exchange_uses_expected_fields_and_returns_only_token(self):
        opener = Mock()
        opener.open.return_value = TokenResponse(b'{"access_token":"token-secret","refresh_token":"unused"}')
        with patch.object(auth.urllib.request, "build_opener", return_value=opener) as build:
            token = auth.exchange_code("client", "client-secret", "code-secret", "v" * 43)
        self.assertEqual(token, "token-secret")
        self.assertIsInstance(build.call_args.args[0], auth.NoRedirect)
        request = opener.open.call_args.args[0]
        fields = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(request.full_url, auth.TOKEN_URL)
        self.assertEqual(fields["redirect_uri"], [auth.REDIRECT_URI])
        self.assertEqual(fields["audience"], [auth.AUDIENCE])
        self.assertEqual(fields["code_verifier"], ["v" * 43])
        self.assertEqual(fields["client_secret"], ["client-secret"])

    def test_token_errors_do_not_expose_response(self):
        for error in (
            urllib.error.HTTPError(auth.TOKEN_URL, 401, "secret-body", {}, None),
            urllib.error.URLError("secret-detail"),
        ):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(auth.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError) as failure:
                    auth.exchange_code("client", "secret", "code", "verifier")
            self.assertNotIn("secret", str(failure.exception))

    def test_invalid_token_response_rejected(self):
        for body in (b"not json", json.dumps({"refresh_token": "secret"}).encode()):
            opener = Mock()
            opener.open.return_value = TokenResponse(body)
            with patch.object(auth.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError) as failure:
                    auth.exchange_code("client", "secret", "code", "verifier")
            self.assertNotIn("secret", str(failure.exception))

    def test_main_passes_access_token_directly_to_fetch_without_printing(self):
        stdout = io.StringIO()
        with patch.object(auth, "read_masked", side_effect=["client", "client-secret"]), \
             patch.object(auth, "wait_for_code", return_value="code-secret"), \
             patch.object(auth, "exchange_code", return_value="token-secret"), \
             patch.object(auth, "fetch_first_page", return_value=b"private-data") as fetch, \
             patch.object(auth, "save_response", return_value=".private/result.bin") as save, \
             contextlib.redirect_stdout(stdout):
            auth.main([])
        fetch.assert_called_once_with("token-secret")
        save.assert_called_once_with(b"private-data")
        for secret in ("client-secret", "code-secret", "token-secret", "private-data"):
            self.assertNotIn(secret, stdout.getvalue())

    def test_invalid_masked_input_stops_before_authorization(self):
        with patch.object(auth, "read_masked", side_effect=auth.PrivateInputError("Unsupported control character")), \
             patch.object(auth, "wait_for_code") as wait:
            with self.assertRaisesRegex(SystemExit, "Unsupported control character"):
                auth.main([])
        wait.assert_not_called()

    def test_file_credentials_are_used_only_after_confirmation(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.env"
            original = b"CLIENT_ID=synthetic-client\nCLIENT_SECRET=synthetic-secret\n"
            path.write_bytes(original)
            output = io.StringIO()
            with patch.object(auth.sys, "stdin", terminal), patch.object(auth.sys, "__stdin__", terminal), \
                 patch.object(auth.sys, "stderr", stderr), \
                 patch("builtins.input", return_value="CONTINUE"), \
                 patch.object(auth, "read_masked") as masked, \
                 patch.object(auth, "wait_for_code", return_value="code-secret") as wait, \
                 patch.object(auth, "exchange_code", return_value="access-secret") as exchange, \
                 patch.object(auth, "fetch_first_page", return_value=b"private-response"), \
                 patch.object(auth, "save_response", return_value=".private/result.bin"), \
                 contextlib.redirect_stdout(output):
                auth.main(["--env-file", str(path)])
            masked.assert_not_called()
            self.assertEqual(wait.call_args.args[0], "synthetic-client")
            self.assertEqual(exchange.call_args.args[:3],
                             ("synthetic-client", "synthetic-secret", "code-secret"))
            self.assertEqual(path.read_bytes(), original)
            for value in ("synthetic-client", "synthetic-secret", "code-secret",
                          "access-secret", "private-response", str(path)):
                self.assertNotIn(value, output.getvalue())

    def test_file_credentials_cancellation_never_reads_or_authorizes(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with patch.object(auth.sys, "stdin", terminal), patch.object(auth.sys, "__stdin__", terminal), \
             patch.object(auth.sys, "stderr", stderr), patch("builtins.input", return_value=""), \
             patch.object(auth, "load_local_credentials") as load, \
             patch.object(auth, "wait_for_code") as wait, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "Canceled"):
                auth.main(["--env-file", "unused"])
        load.assert_not_called()
        wait.assert_not_called()

    def test_file_credentials_reject_noninteractive_without_reading(self):
        terminal = Mock()
        terminal.isatty.return_value = False
        with patch.object(auth.sys, "stdin", terminal), patch.object(auth.sys, "__stdin__", terminal), \
             patch.object(auth, "load_local_credentials") as load, \
             patch.object(auth, "wait_for_code") as wait:
            with self.assertRaisesRegex(SystemExit, "private interactive terminal"):
                auth.main(["--env-file", "unused"])
        load.assert_not_called()
        wait.assert_not_called()

    def test_invalid_file_is_not_disclosed_or_authorized(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.env"
            path.write_text("CLIENT_ID=synthetic-id\nCLIENT_SECRET='synthetic-secret'\n", encoding="utf-8")
            with patch.object(auth.sys, "stdin", terminal), patch.object(auth.sys, "__stdin__", terminal), \
                 patch.object(auth.sys, "stderr", stderr), patch("builtins.input", return_value="CONTINUE"), \
                 patch.object(auth, "wait_for_code") as wait, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    auth.main(["--env-file", str(path)])
            for value in ("synthetic-id", "synthetic-secret", str(path)):
                self.assertNotIn(value, str(failure.exception))
            wait.assert_not_called()

    def test_renew_rejects_env_file_without_reading_or_request(self):
        with patch.object(auth, "load_local_credentials") as load, \
             patch.object(auth, "renew") as renew, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                auth.main(["--renew", "--env-file", "unused"])
        load.assert_not_called()
        renew.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_offline_setup_can_use_confirmed_file_credentials(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        stderr = Mock()
        stderr.isatty.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.env"
            path.write_text("CLIENT_ID=client\nCLIENT_SECRET=synthetic-secret\n", encoding="utf-8")
            store = auth.TokenStore(Path(directory) / "store")
            with patch.object(auth.sys, "stdin", terminal), patch.object(auth.sys, "__stdin__", terminal), \
                 patch.object(auth.sys, "stderr", stderr), patch("builtins.input", return_value="CONTINUE"), \
                 patch.object(auth, "TokenStore", return_value=store), \
                 patch.object(auth, "read_masked") as masked, \
                 patch.object(auth, "wait_for_code", return_value="code") as wait, \
                 patch.object(auth, "exchange_code", return_value={"refresh_token": "refresh-secret"}) as exchange, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                auth.main(["--offline-setup", "--env-file", str(path)])
            masked.assert_not_called()
            self.assertTrue(wait.call_args.kwargs["offline"])
            self.assertEqual(exchange.call_args.args[:3], ("client", "synthetic-secret", "code"))
            self.assertNotIn("synthetic-secret", output.getvalue())
            with store.locked():
                self.assertEqual(store.read(), ("client", "refresh-secret", "ready"))

    def test_offline_scope_is_explicit(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(
            auth.authorization_url("client", "state", "v" * 43, offline=True)
        ).query)
        self.assertEqual(set(query["scope"][0].split()),
                         {"openid", "vehicle_charging_cmds", "offline_access"})

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_setup_saves_only_offline_credentials_and_wont_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            output = io.StringIO()
            with patch.object(auth, "read_masked", side_effect=["client", "client-secret"]), \
                 patch.object(auth, "wait_for_code", return_value="code") as wait, \
                 patch.object(auth, "exchange_code", return_value={
                     "access_token": "access-secret", "refresh_token": "refresh-secret"
                 }), contextlib.redirect_stdout(output):
                auth.offline_setup(store)
                with self.assertRaisesRegex(ValueError, "already exist"):
                    auth.offline_setup(store)
            wait.assert_called_once()
            self.assertTrue(wait.call_args.kwargs["offline"])
            with store.locked():
                self.assertEqual(store.read(), ("client", "refresh-secret", "ready"))
            for value in ("client-secret", "access-secret", "refresh-secret"):
                self.assertNotIn(value, output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_renew_saves_rotation_before_fetch_with_documented_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("synthetic-client", "old-secret")
            opener = Mock()
            opener.open.return_value = TokenResponse(
                b'{"access_token":"access-secret","refresh_token":"new-secret"}')

            def fetch(token):
                self.assertEqual(token, "access-secret")
                self.assertEqual(store.read(), ("synthetic-client", "new-secret", "ready"))
                return b"private-response"

            stdout = io.StringIO()
            with patch.object(auth.urllib.request, "build_opener", return_value=opener) as build, \
                 patch.object(auth, "fetch_first_page", side_effect=fetch), \
                 patch.object(auth, "save_response", return_value=".private/result.bin"), \
                 contextlib.redirect_stdout(stdout):
                auth.renew(store)
            self.assertIsInstance(build.call_args.args[0], auth.NoRedirect)
            request = opener.open.call_args.args[0]
            self.assertEqual(urllib.parse.parse_qs(request.data.decode()), {
                "grant_type": ["refresh_token"], "client_id": ["synthetic-client"],
                "refresh_token": ["old-secret"],
            })
            for secret in ("old-secret", "new-secret", "access-secret", "private-response"):
                self.assertNotIn(secret, stdout.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_failed_refresh_leaves_pending_and_blocks_future_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("client", "old-secret")
            with patch.object(auth, "token_request", side_effect=ValueError("HTTP 400")) as request, \
                 patch.object(auth, "fetch_first_page") as fetch:
                with self.assertRaisesRegex(ValueError, "uncertain"):
                    auth.renew(store)
                with self.assertRaisesRegex(ValueError, "manual recovery"):
                    auth.renew(store)
            request.assert_called_once()
            fetch.assert_not_called()
            with store.locked():
                self.assertEqual(store.read(), ("client", "old-secret", "pending"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_invalid_rotated_token_prevents_fetch_and_leaves_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("client", "old-secret")
            with patch.object(auth, "token_request", return_value={
                "access_token": "access-secret", "refresh_token": "secret\n"
            }), patch.object(auth, "fetch_first_page") as fetch:
                with self.assertRaises(ValueError) as failure:
                    auth.renew(store)
            self.assertNotIn("secret", str(failure.exception))
            fetch.assert_not_called()
            with store.locked():
                self.assertEqual(store.read(), ("client", "old-secret", "pending"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_rotation_survives_missing_access_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("client", "old-secret")
            with patch.object(auth, "token_request", return_value={"refresh_token": "new-secret"}), \
                 patch.object(auth, "fetch_first_page") as fetch:
                with self.assertRaisesRegex(ValueError, "access token"):
                    auth.renew(store)
            fetch.assert_not_called()
            with store.locked():
                self.assertEqual(store.read(), ("client", "new-secret", "ready"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_pending_write_failure_stops_before_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("client", "old-secret")
            with patch.object(store, "write", side_effect=ValueError("storage unavailable")), \
                 patch.object(auth, "token_request") as request:
                with self.assertRaisesRegex(ValueError, "storage unavailable"):
                    auth.renew(store)
            request.assert_not_called()
            with store.locked():
                self.assertEqual(store.read(), ("client", "old-secret", "ready"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_http_failure_does_not_expose_body_and_blocks_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with store.locked():
                store.write("client", "old-secret")
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError(
                auth.TOKEN_URL, 401, "body-secret", {}, io.BytesIO(b"response-secret"))
            with patch.object(auth.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError) as failure:
                    auth.renew(store)
            for value in ("old-secret", "body-secret", "response-secret"):
                self.assertNotIn(value, str(failure.exception))
            self.assertIn("HTTP 401", str(failure.exception))
            with store.locked():
                self.assertEqual(store.read(), ("client", "old-secret", "pending"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_offline_setup_requires_private_interactive_input(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.TokenStore(directory)
            with patch.object(auth, "TokenStore", return_value=store), \
                 patch.object(auth, "read_masked", side_effect=EOFError), \
                 patch.object(auth, "wait_for_code") as wait, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "private interactive terminal"):
                    auth.main(["--offline-setup"])
            wait.assert_not_called()
            self.assertFalse(store.path.exists())

    def test_renew_mode_never_prompts(self):
        store = Mock()
        with patch.object(auth, "TokenStore", return_value=store), \
             patch.object(auth, "renew") as renew, \
             patch.object(auth, "read_masked") as prompt:
            auth.main(["--renew"])
        renew.assert_called_once_with(store)
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
