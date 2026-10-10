import sqlite3
import os
import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import auth_mailer
import auth_store
import gmail_mcp_server
import gmail_oauth
import web_app


class FakeHost:
    async def start(self):
        return None

    async def close(self):
        return None

    async def respond(self, message, history):
        return f"echo: {message}"

    async def get_unread_count(self):
        return 2


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "auth.sqlite3"
        self.database_patcher = patch.object(auth_store, "AUTH_DATABASE_PATH", database_path)
        self.host_patcher = patch.object(web_app, "MCPChatHost", FakeHost)
        self.database_patcher.start()
        self.host_patcher.start()
        self.client = TestClient(web_app.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.host_patcher.stop()
        self.database_patcher.stop()
        self.temporary_directory.cleanup()

    def csrf_headers(self):
        return {"X-CSRF-Token": self.client.cookies.get(web_app.CSRF_COOKIE, "")}

    def signup(self, *, email="member@example.net", password="Correct-Horse-42!"):
        self.client.get("/signup")
        return self.client.post(
            "/auth/signup",
            json={
                "full_name": "Test Member",
                "email": email,
                "password": password,
                "password_confirmation": password,
            },
            headers=self.csrf_headers(),
        )

    def test_oauth_state_and_credentials_use_persistent_user_records(self):
        response = self.signup()
        user_id = response.json()["user"]["id"]

        state = auth_store.create_oauth_state(user_id)
        self.assertEqual(auth_store.consume_oauth_state(state), user_id)
        self.assertIsNone(auth_store.consume_oauth_state(state))

        auth_store.store_gmail_credentials(user_id, "encrypted-credential-payload")
        self.assertEqual(
            auth_store.get_gmail_credentials(user_id),
            "encrypted-credential-payload",
        )
        auth_store.delete_gmail_credentials(user_id)
        self.assertIsNone(auth_store.get_gmail_credentials(user_id))

    def test_vercel_requires_postgres_without_creating_local_auth_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-created" / "auth.sqlite3"
            with patch.object(auth_store, "AUTH_DATABASE_PATH", path):
                with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                        auth_store.initialize_auth_database()
            self.assertFalse(path.parent.exists())

    def test_postgres_account_insert_uses_returning_identity_and_bound_parameters(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 42}
        connection.execute.return_value = cursor
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://example.invalid/test"}),
            patch("psycopg.connect", return_value=connection),
        ):
            user = auth_store.create_user("Test User", "TEST@example.net", "argon-hash")

        self.assertEqual(user["id"], 42)
        query, parameters = connection.execute.call_args.args
        self.assertIn("RETURNING id", query)
        self.assertIn("%s", query)
        self.assertEqual(parameters[1], "test@example.net")
        self.assertNotIn("TEST@example.net", query)

    def test_vercel_gmail_connect_returns_google_web_oauth_url(self):
        self.assertEqual(self.signup().status_code, 200)
        session = auth_store.get_session(self.client.cookies.get(web_app.SESSION_COOKIE))
        authorization_url = "https://accounts.google.com/o/oauth2/auth?state=test-state"
        with (
            patch.dict(os.environ, {"VERCEL": "1"}),
            patch.object(web_app, "_current_session", return_value=session),
            patch.object(auth_store, "create_oauth_state", return_value="test-state"),
            patch("gmail_oauth.authorization_url", return_value=authorization_url),
        ):
            response = self.client.post(
                "/api/gmail/connect",
                headers=self.csrf_headers(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authorization_url": authorization_url})

    def test_gmail_oauth_callback_stores_encrypted_credentials_and_redirects(self):
        self.assertEqual(self.signup().status_code, 200)
        user_id = auth_store.get_session(
            self.client.cookies.get(web_app.SESSION_COOKIE)
        )["id"]
        with (
            patch.object(auth_store, "consume_oauth_state", return_value=user_id),
            patch.object(auth_store, "store_gmail_credentials") as store_credentials,
            patch("gmail_oauth.exchange_code", return_value="encrypted-credential-data"),
        ):
            response = self.client.get(
                "/api/gmail/oauth/callback?state=single-use-state&code=oauth-code",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        store_credentials.assert_called_once_with(user_id, "encrypted-credential-data")
        self.assertNotIn("oauth-code", response.text)

    def test_gmail_credentials_are_encrypted_with_configured_fernet_key(self):
        from cryptography.fernet import Fernet

        credentials_json = '{"refresh_token":"never-return-this"}'
        key = Fernet.generate_key().decode("ascii")
        with patch.dict(os.environ, {"GMAIL_TOKEN_ENCRYPTION_KEY": key}):
            ciphertext = gmail_oauth.encrypt_credentials(credentials_json)
            self.assertEqual(gmail_oauth.decrypt_credentials(ciphertext), credentials_json)
        self.assertNotIn("never-return-this", ciphertext)

    def test_vercel_password_reset_uses_https_provider(self):
        environment = {
            "VERCEL": "1",
            "RESEND_API_KEY": "test-resend-key",
            "RESEND_FROM_EMAIL": "SASPAL <no-reply@example.test>",
            "APP_BASE_URL": "https://saspal-mcp.vercel.app",
        }
        response = MagicMock()
        with (
            patch.dict(os.environ, environment),
            patch("auth_mailer.httpx.post", return_value=response) as send_request,
        ):
            auth_mailer.send_password_reset_email("user@example.test", "reset-token")

        self.assertEqual(send_request.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(
            send_request.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-resend-key",
        )
        response.raise_for_status.assert_called_once()

    def test_pending_email_draft_is_owner_scoped_and_single_use(self):
        response = self.signup()
        user_id = response.json()["user"]["id"]
        draft_id = "reviewable-draft-token"
        auth_store.store_pending_email_draft(
            user_id,
            draft_id,
            "recipient@example.test",
            "Project update",
            "The meeting is Monday.",
        )

        self.assertIsNone(auth_store.consume_pending_email_draft(user_id + 1, draft_id))
        self.assertEqual(
            auth_store.consume_pending_email_draft(user_id, draft_id),
            {
                "to": "recipient@example.test",
                "subject": "Project update",
                "body": "The meeting is Monday.",
            },
        )
        self.assertIsNone(auth_store.consume_pending_email_draft(user_id, draft_id))

    def test_vercel_pending_draft_is_encrypted_and_decrypted_on_consumption(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        connection = MagicMock()
        returned_draft = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = auth_store._ConnectionAdapter(
            connection,
            postgres=False,
        )
        connection.execute.side_effect = [MagicMock(), MagicMock(), returned_draft]
        with patch.dict(
            os.environ,
            {"VERCEL": "1", "GMAIL_TOKEN_ENCRYPTION_KEY": key},
        ), patch("auth_store._connection", return_value=context):
            auth_store.store_pending_email_draft(
                5,
                "draft-id",
                "private-recipient@example.test",
                "Private subject",
                "Private body",
            )
            insert_parameters = connection.execute.call_args_list[1].args[1]
            encrypted_draft = insert_parameters[5]
            returned_draft.fetchone.return_value = {
                "recipient": "",
                "subject": "",
                "body": "",
                "draft_ciphertext": encrypted_draft,
            }
            draft = auth_store.consume_pending_email_draft(5, "draft-id")

        self.assertEqual(insert_parameters[2:5], ("", "", ""))
        self.assertNotIn("Private body", encrypted_draft)
        self.assertEqual(
            draft,
            {
                "to": "private-recipient@example.test",
                "subject": "Private subject",
                "body": "Private body",
            },
        )

    def test_login_redirect_and_gmail_apis_require_authentication(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=/")
        self.assertEqual(self.client.get("/static/index.html", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/api/recent-emails").status_code, 401)
        self.assertEqual(self.client.post("/chat", json={"message": "hello"}).status_code, 403)

    def test_signup_hashes_password_and_duplicate_email_is_rejected(self):
        response = self.signup()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password", response.text.lower())
        self.assertNotIn("password_hash", response.text.lower())
        self.assertIn("httponly", response.headers.get("set-cookie", "").lower())
        self.assertIn("samesite=lax", response.headers.get("set-cookie", "").lower())

        connection = sqlite3.connect(auth_store.AUTH_DATABASE_PATH)
        try:
            stored_hash = connection.execute(
                "SELECT password_hash FROM users WHERE email = ?", ("member@example.net",)
            ).fetchone()[0]
            stored_session = connection.execute(
                "SELECT token_hash FROM auth_sessions"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertNotEqual(stored_hash, "Correct-Horse-42!")
        self.assertTrue(stored_hash.startswith("$argon2id$"))
        self.assertNotEqual(stored_session, self.client.cookies.get(web_app.SESSION_COOKIE))

        duplicate = self.client.post(
            "/auth/signup",
            json={
                "full_name": "Another Member",
                "email": "MEMBER@example.net",
                "password": "Another-Correct-42!",
                "password_confirmation": "Another-Correct-42!",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_password_confirmation_and_auth_validation_do_not_echo_secrets(self):
        self.client.get("/signup")
        secret = "Sensitive-Passphrase-99!"
        response = self.client.post(
            "/auth/signup",
            json={
                "full_name": "Test Member",
                "email": "member@example.net",
                "password": secret,
                "password_confirmation": "Different-Passphrase-88!",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        self.assertEqual(response.json(), {"detail": "Invalid authentication request."})

    def test_gmail_connection_requires_an_explicit_connect_step(self):
        self.assertEqual(self.signup().status_code, 200)

        response = self.client.get("/connect-gmail")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Connect Gmail", response.text)

        with patch.object(web_app, "gmail_is_connected", return_value=False):
            status = self.client.get("/api/gmail-status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json(), {"connected": False})

        with patch.object(web_app, "gmail_is_connected", return_value=True):
            status = self.client.get("/api/gmail-status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json(), {"connected": True})

        with patch.object(web_app, "connect_gmail", return_value=True):
            connect = self.client.post("/api/gmail/connect", headers=self.csrf_headers())
            self.assertEqual(connect.status_code, 200)
            self.assertTrue(connect.json()["connected"])

    def test_login_protected_api_and_logout(self):
        self.assertEqual(self.signup().status_code, 200)
        self.assertEqual(self.client.get("/auth/me").json()["email"], "member@example.net")
        self.assertEqual(self.client.get("/api/unread-count").json(), {"count": 2})

        response = self.client.post(
            "/auth/logout", json={}, headers=self.csrf_headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"logged_out": True})
        self.assertEqual(self.client.get("/api/unread-count").status_code, 401)

        self.client.get("/login")
        bad_login = self.client.post(
            "/auth/login",
            json={"email": "member@example.net", "password": "Wrong-Passphrase-55!"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(bad_login.status_code, 401)
        self.assertEqual(bad_login.json()["detail"], "Email or password is incorrect.")

        good_login = self.client.post(
            "/auth/login",
            json={"email": "MEMBER@example.net", "password": "Correct-Horse-42!"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(good_login.status_code, 200)
        self.assertEqual(self.client.get("/api/unread-count").status_code, 200)

    def test_csrf_required_for_mutations(self):
        self.client.get("/signup")
        response = self.client.post(
            "/auth/signup",
            json={
                "full_name": "Test Member",
                "email": "member@example.net",
                "password": "Correct-Horse-42!",
                "password_confirmation": "Correct-Horse-42!",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_csrf_allows_runtime_host_port(self):
        self.client.get("/signup")
        response = self.client.post(
            "/auth/signup",
            json={
                "full_name": "Test Member",
                "email": "member@example.net",
                "password": "Correct-Horse-42!",
                "password_confirmation": "Correct-Horse-42!",
            },
            headers={**self.csrf_headers(), "Origin": "http://testserver"},
        )
        self.assertEqual(response.status_code, 200)

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        self.assertEqual(self.signup().status_code, 200)
        with (
            patch.object(auth_mailer, "is_configured", return_value=True),
            patch.object(auth_mailer, "send_password_reset_email") as send_email,
        ):
            response = self.client.post(
                "/auth/forgot-password",
                json={"email": "member@example.net"},
                headers=self.csrf_headers(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["message"],
            "If an account matches that address, a reset link will be sent.",
        )
        token = send_email.call_args.args[1]

        self.client.get("/forgot-password")
        reset = self.client.post(
            "/auth/reset-password",
            json={
                "token": token,
                "password": "Brand-New-Passphrase-77!",
                "password_confirmation": "Brand-New-Passphrase-77!",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.client.get("/api/unread-count").status_code, 401)

        replay = self.client.post(
            "/auth/reset-password",
            json={
                "token": token,
                "password": "Another-New-Passphrase-66!",
                "password_confirmation": "Another-New-Passphrase-66!",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(replay.status_code, 400)

        login = self.client.post(
            "/auth/login",
            json={"email": "member@example.net", "password": "Brand-New-Passphrase-77!"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(login.status_code, 200)

    def test_reset_email_keeps_token_in_fragment_without_sending_mail(self):
        smtp_client = MagicMock()
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = smtp_client
        with (
            patch.dict(
                "os.environ",
                {
                    "SMTP_HOST": "smtp.invalid",
                    "SMTP_PORT": "587",
                    "SMTP_FROM_EMAIL": "noreply@example.invalid",
                    "APP_BASE_URL": "https://assistant.example.invalid",
                },
            ),
            patch("auth_mailer.smtplib.SMTP", return_value=smtp_context),
        ):
            auth_mailer.send_password_reset_email("member@example.invalid", "opaque-reset-token")

        message = smtp_client.send_message.call_args.args[0]
        reset_url = next(
            line.removeprefix("Use this one-time link within 30 minutes: ")
            for line in message.get_content().splitlines()
            if line.startswith("Use this one-time link")
        )
        parsed_url = urlparse(reset_url)
        self.assertEqual(parsed_url.query, "")
        self.assertEqual(parse_qs(parsed_url.fragment)["token"], ["opaque-reset-token"])

    def test_recovery_delivery_failure_does_not_reveal_account_existence(self):
        self.assertEqual(self.signup().status_code, 200)
        with (
            patch.object(auth_mailer, "is_configured", return_value=True),
            patch.object(auth_mailer, "send_password_reset_email", side_effect=RuntimeError),
        ):
            response = self.client.post(
                "/auth/forgot-password",
                json={"email": "member@example.net"},
                headers=self.csrf_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["message"],
            "If an account matches that address, a reset link will be sent.",
        )
        connection = sqlite3.connect(auth_store.AUTH_DATABASE_PATH)
        try:
            remaining_tokens = connection.execute(
                "SELECT COUNT(*) FROM password_reset_tokens"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(remaining_tokens, 0)


if __name__ == "__main__":
    unittest.main()
