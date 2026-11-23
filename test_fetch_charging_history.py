import contextlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import fetch_charging_history as fetch


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, size):
        return self.body[:size]


class FetchTests(unittest.TestCase):
    def test_saves_first_page_without_printing_token_or_body(self):
        body = b'{"example":"private data"}'
        opener = Mock()
        opener.open.return_value = Response(body)
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch.object(fetch, "ROOT", Path(directory)), \
                 patch.dict("os.environ", {"TESLA_FLEET_ACCESS_TOKEN": "secret-token"}), \
                 patch.object(fetch.urllib.request, "build_opener", return_value=opener), \
                 contextlib.redirect_stdout(output):
                fetch.main()
            saved = list((Path(directory) / ".private").glob("charging-history-*.bin"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_bytes(), body)
            self.assertNotIn("secret-token", output.getvalue())
            self.assertNotIn("private data", output.getvalue())
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, fetch.URL)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

    def test_missing_or_invalid_token_does_not_make_request(self):
        with patch.object(fetch.urllib.request, "build_opener") as build:
            for token in (None, "", "secret\nvalue", "secret value"):
                with self.subTest(token=token), self.assertRaises(ValueError):
                    fetch.fetch_first_page(token)
            build.assert_not_called()

    def test_http_and_network_failures_do_not_expose_details_or_save(self):
        errors = [
            urllib.error.HTTPError(fetch.URL, 401, "secret body", {}, None),
            urllib.error.HTTPError(fetch.URL, 302, "redirect", {}, None),
            urllib.error.URLError("secret network detail"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(fetch, "ROOT", Path(directory)), \
                     patch.dict("os.environ", {"TESLA_FLEET_ACCESS_TOKEN": "secret-token"}), \
                     patch.object(fetch.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(SystemExit) as failure:
                        fetch.main()
                self.assertNotIn("secret", str(failure.exception))
                self.assertFalse((Path(directory) / ".private").exists())
                if isinstance(error, urllib.error.HTTPError):
                    self.assertIn(f"HTTP {error.code}", str(failure.exception))

    def test_redirect_handler_never_follows_location(self):
        self.assertIsNone(fetch.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_oversized_response_is_not_saved(self):
        opener = Mock()
        opener.open.return_value = Response(b"x" * (fetch.MAX_BYTES + 1))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(fetch, "ROOT", Path(directory)), \
             patch.dict("os.environ", {"TESLA_FLEET_ACCESS_TOKEN": "token"}), \
             patch.object(fetch.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(SystemExit, "exceeds 10 MiB"):
                fetch.main()
            self.assertFalse((Path(directory) / ".private").exists())


if __name__ == "__main__":
    unittest.main()
