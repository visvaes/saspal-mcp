import unittest

from gmail_mcp_server import _gmail_auth_error_message


class GmailAuthMessageTests(unittest.TestCase):
    def test_invalid_scope_message_is_actionable(self) -> None:
        message = _gmail_auth_error_message("invalid_scope: Bad Request")
        self.assertIn("reauthorize", message.lower())
        self.assertTrue("token" in message.lower() or "oauth" in message.lower())


if __name__ == "__main__":
    unittest.main()
