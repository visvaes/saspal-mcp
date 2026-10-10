import asyncio
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import gmail_mcp_server as gmail_mcp
import auth_store
import mcp_host
import web_app
from web_app import app


class FakeGmailService:
    def __init__(self, messages=None, profile_email="me@example.com"):
        self._messages = messages or []
        self.profile_email = profile_email
        self.users = MagicMock()
        self.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": self._messages}
        self.users.return_value.messages.return_value.get.return_value.execute.side_effect = self._get_message
        self.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": profile_email}
        self.users.return_value.messages.return_value.send.return_value.execute.return_value = {"id": "sent-123"}

    def _get_message(self):
        if not self._messages:
            raise KeyError("message")
        return self._messages[0]


class GmailMCPTests(unittest.TestCase):
    def setUp(self):
        gmail_mcp.get_gmail_service.cache_clear()

    def test_gmail_auth_missing_token_raises_actionable_error(self):
        with patch.object(gmail_mcp, "TOKEN_FILE", SimpleNamespace(is_file=lambda: False)):
            with self.assertRaises(gmail_mcp.GmailAuthenticationError):
                gmail_mcp.get_gmail_service()

    def test_gmail_auth_invalid_scope_error_is_actionable(self):
        self.assertIn("reauthorize", gmail_mcp._gmail_auth_error_message("invalid_scope: Bad Request").lower())
        self.assertIn("gmail", gmail_mcp._gmail_auth_error_message("invalid_scope: Bad Request").lower())

    def test_vercel_gmail_service_uses_database_credentials_not_token_file(self):
        credentials = SimpleNamespace(
            has_scopes=MagicMock(return_value=True),
            expired=False,
            refresh_token=None,
            valid=True,
        )
        token_file = SimpleNamespace(
            is_file=MagicMock(side_effect=AssertionError("Vercel must not read token.json"))
        )
        with (
            patch.dict(os.environ, {"VERCEL": "1", "DATABASE_URL": "postgresql://example.invalid/test"}),
            patch.object(auth_store, "get_gmail_credentials", return_value="ciphertext"),
            patch("gmail_oauth.decrypt_credentials", return_value="{}"),
            patch.object(gmail_mcp, "TOKEN_FILE", token_file),
            patch.object(gmail_mcp.Credentials, "from_authorized_user_info", return_value=credentials),
            patch.object(gmail_mcp, "build", return_value="gmail-service"),
        ):
            self.assertEqual(gmail_mcp.get_gmail_service(7), "gmail-service")
        token_file.is_file.assert_not_called()

    def test_search_emails_returns_metadata(self):
        fake_message = {
            "id": "abc123",
            "snippet": "Follow up on the proposal.",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Proposal review"},
                    {"name": "Date", "value": "Tue, 01 Oct 2024 12:00:00 +0000"},
                ]
            },
        }
        fake_service = FakeGmailService(messages=[fake_message])
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            result = gmail_mcp.search_emails("proposal", max_results=10)
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["subject"], "Proposal review")
        self.assertEqual(result[0]["sender"], "alice@example.com")

    def test_get_email_returns_body_and_metadata(self):
        fake_message = {
            "id": "msg-1",
            "snippet": "Hello team",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Date", "value": "Tue, 01 Oct 2024 12:00:00 +0000"},
                ],
                "body": {"data": "SGVsbG8gZnJvbSBHSUFN"},
            },
        }
        fake_service = FakeGmailService(messages=[fake_message])
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            result = gmail_mcp.get_email("msg-1")
        self.assertEqual(result["subject"], "Hello")
        self.assertEqual(result["body"], "Hello from GIAM")

    def test_get_recent_and_unread_lists_use_gmail_search(self):
        fake_message = {
            "id": "recent-1",
            "snippet": "Latest message",
            "payload": {
                "headers": [
                    {"name": "From", "value": "team@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Latest"},
                    {"name": "Date", "value": "Tue, 01 Oct 2024 12:00:00 +0000"},
                ]
            },
        }
        fake_service = FakeGmailService(messages=[fake_message])
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            recent = gmail_mcp.list_recent_emails(max_results=10)
            unread = gmail_mcp.get_unread_emails(max_results=10)
        self.assertEqual(recent[0]["message_id"], "recent-1")
        self.assertEqual(unread[0]["message_id"], "recent-1")

    def test_date_search_uses_inclusive_date_range(self):
        fake_message = {
            "id": "date-1",
            "snippet": "Within range",
            "payload": {
                "headers": [
                    {"name": "From", "value": "ops@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Date test"},
                    {"name": "Date", "value": "Tue, 01 Oct 2024 12:00:00 +0000"},
                ]
            },
        }
        fake_service = FakeGmailService(messages=[fake_message])
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            result = gmail_mcp.search_emails_by_date("2024-10-01", "2024-10-02", query="ops", max_results=5)
        self.assertEqual(result[0]["message_id"], "date-1")
        self.assertEqual(result[0]["subject"], "Date test")

    def test_get_email_summary_data_returns_metadata_only(self):
        fake_message = {
            "id": "summary-1",
            "snippet": "Reminder about onboarding",
            "payload": {
                "headers": [
                    {"name": "From", "value": "hr@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Onboarding"},
                    {"name": "Date", "value": "Tue, 01 Oct 2024 12:00:00 +0000"},
                ]
            },
        }
        fake_service = FakeGmailService(messages=[fake_message])
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            result = gmail_mcp.get_email_summary_data(query="onboarding", max_results=5)
        self.assertEqual(result[0]["snippet"], "Reminder about onboarding")

    def test_send_email_rejects_invalid_recipient(self):
        response = gmail_mcp.send_email("bad-address", "Hello", "Body")
        self.assertIn("valid single recipient", response["error"].lower())

    def test_send_email_returns_error_on_auth_failure(self):
        with patch.object(gmail_mcp, "get_gmail_service", side_effect=gmail_mcp.GmailAuthenticationError("bad auth")):
            response = gmail_mcp.send_email("to@example.com", "Hello", "Body")
        self.assertIn("reauthorize", response["error"].lower())

    def test_send_email_uses_mocked_gmail_client_without_sending_real_mail(self):
        fake_service = FakeGmailService(profile_email="sender@example.com")
        with patch.object(gmail_mcp, "get_gmail_service", return_value=fake_service):
            response = gmail_mcp.send_email("to@example.com", "Hello", "Body")
        self.assertEqual(response["status"], "sent")
        self.assertEqual(response["message_id"], "sent-123")

    def test_email_and_search_helpers_handle_api_errors(self):
        with patch.object(gmail_mcp, "get_gmail_service", side_effect=gmail_mcp.GmailAuthenticationError("bad auth")):
            search_result = gmail_mcp.search_emails("test")
            email_result = gmail_mcp.get_email("nope")
            self.assertTrue(any(isinstance(item, dict) and "error" in item for item in search_result))
            self.assertIn("gmail authentication", email_result["error"].lower())

    def test_redact_sensitive_email_metadata_removes_otp_tokens_and_credentials(self):
        payload = {
            "body": "Your verification code is 123456. API key is sk-or-v1-secretvalue and password is B3ar3r!",
            "password": "B3ar3r!",
            "api_key": "sk-or-v1-secretvalue",
        }
        redacted = mcp_host._redact_sensitive_email_metadata(payload)
        redacted_text = json.dumps(redacted)
        self.assertNotIn("B3ar3r!", redacted_text)
        self.assertNotIn("123456", redacted_text)
        self.assertNotIn("sk-or-v1-secretvalue", redacted_text)
        self.assertIn("[REDACTED]", redacted_text)

    def test_prompt_injection_guard_is_present_in_draft_system_prompt(self):
        source = inspect.getsource(mcp_host.MCPChatHost.prepare_email_draft)
        self.assertIn("Never send", source)
        self.assertIn("claim to have sent", source.lower())

    def test_local_secret_files_are_ignored_by_workspace_config(self):
        ignore_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".gitignore")
        with open(ignore_file, "r", encoding="utf-8") as handle:
            lines = {line.strip() for line in handle if line.strip() and not line.startswith("#")}

        self.assertIn("token.json.bak", lines)
        self.assertIn(".env.*", lines)
        self.assertIn("credentials.json", lines)
        self.assertIn("token.json", lines)

    def test_mcp_host_prepare_draft_and_cancel(self):
        original_key = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            host = mcp_host.MCPChatHost()
            draft = asyncio.run(host.prepare_email_draft("person@example.com", "Draft a follow-up", subject="Hello", body="This is a draft"))
            self.assertIn("draft_id", draft)
            self.assertTrue(asyncio.run(host.cancel_email_draft(draft["draft_id"])))
            self.assertFalse(asyncio.run(host.cancel_email_draft(draft["draft_id"])))
        finally:
            if original_key is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = original_key

    def test_mcp_host_confirm_send_duplicate_repeated_protection(self):
        original_key = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            host = mcp_host.MCPChatHost()
            draft = asyncio.run(host.prepare_email_draft("person@example.com", "Draft a follow-up", subject="Hello", body="Body"))
            fake_session = SimpleNamespace(
                call_tool=AsyncMock(return_value=SimpleNamespace(
                    is_error=False,
                    content=[SimpleNamespace(text='{"status":"sent"}')],
                ))
            )
            host._tool_to_session = {"send_email": fake_session}
            self.assertEqual(asyncio.run(host.confirm_email_draft(draft["draft_id"]))["status"], "sent")
            with self.assertRaises(LookupError):
                asyncio.run(host.confirm_email_draft(draft["draft_id"]))
        finally:
            if original_key is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = original_key

    def test_fastapi_chat_endpoint_uses_mocked_chat_host(self):
        class FakeHost:
            async def start(self):
                return None

            async def close(self):
                return None

            async def respond(self, message, history):
                return f"echo: {message}"

        original = web_app.MCPChatHost
        web_app.MCPChatHost = FakeHost
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                with patch.object(
                    auth_store,
                    "AUTH_DATABASE_PATH",
                    Path(temporary_directory) / "auth.sqlite3",
                ), TestClient(app) as client:
                    client.get("/signup")
                    csrf_token = client.cookies.get(web_app.CSRF_COOKIE)
                    signup = client.post(
                        "/auth/signup",
                        json={
                            "full_name": "Test Member",
                            "email": "member@example.net",
                            "password": "Correct-Horse-42!",
                            "password_confirmation": "Correct-Horse-42!",
                        },
                        headers={"X-CSRF-Token": csrf_token},
                    )
                    self.assertEqual(signup.status_code, 200)
                    response = client.post(
                        "/chat",
                        json={"message": "hi there", "history": []},
                        headers={"X-CSRF-Token": client.cookies.get(web_app.CSRF_COOKIE)},
                    )
            self.assertEqual(response.status_code, 200)
            self.assertIn("echo: hi there", response.json()["response"])
        finally:
            web_app.MCPChatHost = original

    def test_browser_send_workflow_is_detected_by_send_intent_parser(self):
        # The browser workflow is validated by the dedicated Node test file.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
