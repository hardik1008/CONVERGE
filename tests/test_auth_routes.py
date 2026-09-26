from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import server as app_server
from app.local_auth import LocalAuthStore


class ProtectedRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_store = app_server.AUTH_STORE
        app_server.AUTH_STORE = LocalAuthStore(Path(self.temp_dir.name) / "local_profiles.json")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app_server.DashboardHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        app_server.AUTH_STORE = self.previous_store
        self.temp_dir.cleanup()

    def request(self, method: str, path: str, body: dict[str, object] | None = None, headers: dict[str, str] | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        payload = json.dumps(body) if body is not None else None
        request_headers = {"Content-Type": "application/json"} if payload is not None else {}
        request_headers.update(headers or {})
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_body, response_headers

    def test_signed_out_user_cannot_open_dashboard_profile_or_forecast_routes(self) -> None:
        dashboard_status, dashboard_body, _ = self.request("GET", "/api/dashboard")
        profile_status, _, _ = self.request("GET", "/api/auth/profile")
        forecast_status, forecast_body, _ = self.request("POST", "/api/forecast", {"csv": ""})

        self.assertEqual(dashboard_status, 401)
        self.assertIn(b"Sign in", dashboard_body)
        self.assertEqual(profile_status, 401)
        self.assertEqual(forecast_status, 401)
        self.assertIn(b"Sign in", forecast_body)

    def test_session_bootstrap_is_available_without_revealing_profile_data(self) -> None:
        status, body, _ = self.request("GET", "/api/auth/session")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["first_run"])
        self.assertIsNone(payload["profile"])
        self.assertEqual(payload["mode"], "local-demo")

    def test_runtime_storage_is_not_served_as_a_static_file(self) -> None:
        status, _, _ = self.request("GET", "/outputs/local_profiles.json")
        self.assertEqual(status, 404)

    def test_forecast_storage_paths_are_scoped_to_profile_identity(self) -> None:
        _, profile_a, _ = app_server.AUTH_STORE.sign_in("ria@example.com", "Ria Rao")
        _, profile_b, _ = app_server.AUTH_STORE.sign_in("dev@example.com", "Dev Shah")

        context_a = app_server.CURRENT_USER_ID.set(profile_a["id"])
        try:
            path_a = app_server.output_path("forecast_summary.json")
        finally:
            app_server.CURRENT_USER_ID.reset(context_a)
        context_b = app_server.CURRENT_USER_ID.set(profile_b["id"])
        try:
            path_b = app_server.output_path("forecast_summary.json")
        finally:
            app_server.CURRENT_USER_ID.reset(context_b)

        self.assertNotEqual(path_a, path_b)
        self.assertEqual(path_a.parent.name, profile_a["id"])
        self.assertEqual(path_b.parent.name, profile_b["id"])

    def test_http_sign_in_restores_and_sign_out_revokes_the_cookie_session(self) -> None:
        status, _, headers = self.request(
            "POST",
            "/api/auth/sign-in",
            {"email": "ria@example.com", "name": "Ria Rao"},
        )
        cookie_header = headers.get("Set-Cookie", "")
        cookie = cookie_header.split(";", 1)[0]
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Strict", cookie_header)
        self.assertIn("Max-Age=1209600", cookie_header)
        self.assertNotIn("Secure", cookie_header)

        session_status, session_body, _ = self.request(
            "GET", "/api/auth/session", headers={"Cookie": cookie}
        )
        self.assertEqual(session_status, 200)
        self.assertTrue(json.loads(session_body)["authenticated"])

        signout_status, _, signout_headers = self.request(
            "POST", "/api/auth/sign-out", headers={"Cookie": cookie}
        )
        self.assertEqual(signout_status, 200)
        self.assertIn("Max-Age=0", signout_headers.get("Set-Cookie", ""))
        final_status, final_body, _ = self.request(
            "GET", "/api/auth/session", headers={"Cookie": cookie}
        )
        self.assertEqual(final_status, 200)
        self.assertFalse(json.loads(final_body)["authenticated"])


if __name__ == "__main__":
    unittest.main()
