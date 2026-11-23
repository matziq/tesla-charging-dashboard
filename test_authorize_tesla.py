import base64
import contextlib
import hashlib
import http.client
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
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
            auth.main()
        fetch.assert_called_once_with("token-secret")
        save.assert_called_once_with(b"private-data")
        for secret in ("client-secret", "code-secret", "token-secret", "private-data"):
            self.assertNotIn(secret, stdout.getvalue())

    def test_invalid_masked_input_stops_before_authorization(self):
        with patch.object(auth, "read_masked", side_effect=auth.PrivateInputError("Unsupported control character")), \
             patch.object(auth, "wait_for_code") as wait:
            with self.assertRaisesRegex(SystemExit, "Unsupported control character"):
                auth.main()
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
