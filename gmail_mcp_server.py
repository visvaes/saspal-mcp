import base64
import binascii
import json
import re
from datetime import date, timedelta
from email.message import EmailMessage as MIMEEmailMessage
from email.policy import SMTP
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from mcp.server import MCPServer


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
SERVER_NAME = "SASPAL Gmail MCP"
PROJECT_ROOT = Path(__file__).resolve().parent
TOKEN_FILE = PROJECT_ROOT / "token.json"
_EMAIL_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)

mcp = MCPServer(SERVER_NAME)


class EmailMetadata(TypedDict):
    """Useful message headers and the Gmail preview snippet."""

    message_id: str
    sender: str
    recipient: str
    subject: str
    date: str
    snippet: str


class EmailMessage(EmailMetadata):
    """Message metadata with a readable text body."""

    body: str


class GmailLabel(TypedDict):
    """Useful identifying fields for a Gmail label."""

    id: str
    name: str
    type: str


class EmailCount(TypedDict):
    """Exact number of Gmail messages matching a query."""

    query: str
    count: int


class GmailAuthenticationError(Exception):
    """Raised when the saved Gmail authorization cannot be used."""


def _clear_stale_token() -> None:
    """Remove a stale/invalid saved OAuth token so the user can reauthorize."""
    try:
        if TOKEN_FILE.is_file():
            TOKEN_FILE.unlink()
    except OSError:
        pass


def _gmail_auth_error_message(error: object | None = None) -> str:
    """Provide a clear recovery hint when Gmail OAuth is missing or stale."""
    text = str(error or "").lower()
    if "invalid_scope" in text or "scope" in text:
        return (
            "Gmail authorization is invalid or stale. Reauthorize the Gmail account "
            "to refresh the OAuth token."
        )
    if "expired" in text or "refresh" in text:
        return "Gmail authorization expired. Reauthorize the Gmail account."
    return "Gmail authentication is unavailable. Reauthorize the Gmail account."


@lru_cache(maxsize=1)
def get_gmail_service() -> Resource:
    """Create and cache a read-only Gmail API service using token.json."""
    if not TOKEN_FILE.is_file():
        raise GmailAuthenticationError("Gmail authentication is unavailable. Reauthorize the Gmail account.")

    try:
        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    except (OSError, ValueError):
        _clear_stale_token()
        raise GmailAuthenticationError(_gmail_auth_error_message("invalid_scope")) from None

    try:
        if not credentials.has_scopes(SCOPES):
            _clear_stale_token()
            raise GmailAuthenticationError(_gmail_auth_error_message("invalid_scope"))
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")

        if not credentials.valid or not credentials.has_scopes(SCOPES):
            _clear_stale_token()
            raise GmailAuthenticationError(_gmail_auth_error_message("invalid_scope"))

        return build("gmail", "v1", credentials=credentials)
    except GoogleAuthError as error:
        _clear_stale_token()
        raise GmailAuthenticationError(_gmail_auth_error_message(error)) from None
    except (OSError, ValueError) as error:
        _clear_stale_token()
        raise GmailAuthenticationError(_gmail_auth_error_message(error)) from None


def _bounded_max_results(max_results: int) -> int:
    """Keep Gmail list requests within the supported POC limit."""
    return max(1, min(max_results, 50))


def _headers(message: dict[str, object]) -> dict[str, str]:
    """Read common Gmail headers without depending on their capitalization."""
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    raw_headers = payload.get("headers", [])
    if not isinstance(raw_headers, list):
        raw_headers = []

    headers: dict[str, str] = {}
    for header in raw_headers:
        if isinstance(header, dict):
            name = str(header.get("name", "")).lower()
            if name in {"from", "to", "subject", "date"}:
                headers[name] = str(header.get("value", ""))
    return headers


def _email_metadata(message: dict[str, object]) -> EmailMetadata:
    """Convert a Gmail API message into the fields exposed to MCP clients."""
    headers = _headers(message)
    return {
        "message_id": str(message.get("id", "")),
        "sender": headers.get("from", ""),
        "recipient": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": str(message.get("snippet", "")),
    }


def _decode_body(data: object) -> str:
    """Decode Gmail's URL-safe base64 message-body representation."""
    if not isinstance(data, str) or not data:
        return ""
    try:
        encoded = data.encode("ascii")
        decoded = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return ""
    return decoded.decode("utf-8", errors="replace")


def _collect_body_parts(payload: dict[str, object]) -> tuple[list[str], list[str]]:
    """Collect plain-text and HTML body parts while skipping attachments."""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    mime_type = str(payload.get("mimeType", "")).lower()

    if not payload.get("filename"):
        body = payload.get("body", {})
        if isinstance(body, dict):
            text = _decode_body(body.get("data"))
            if text and mime_type == "text/plain":
                plain_parts.append(text)
            elif text and mime_type == "text/html":
                html_parts.append(text)

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                plain, html = _collect_body_parts(part)
                plain_parts.extend(plain)
                html_parts.extend(html)
    return plain_parts, html_parts


class _ReadableHTML(HTMLParser):
    """Extract visible text while ignoring script and style content."""

    _BLOCK_TAGS = {
        "address", "article", "blockquote", "br", "div", "h1", "h2", "h3",
        "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "section", "table",
        "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.text_parts.append(data)


def _html_to_text(html: str) -> str:
    """Turn an HTML-only email body into readable, non-markup text."""
    parser = _ReadableHTML()
    parser.feed(html)
    text = "".join(parser.text_parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _message_body(payload: object) -> str:
    """Prefer plain text and fall back to readable text extracted from HTML."""
    if not isinstance(payload, dict):
        return "No readable message body is available."
    plain_parts, html_parts = _collect_body_parts(payload)
    plain_text = "\n\n".join(part.strip() for part in plain_parts if part.strip())
    if plain_text:
        return plain_text

    html_text = "\n\n".join(_html_to_text(part) for part in html_parts)
    return html_text or "No readable message body is available."


def _list_emails(query: str | None, max_results: int) -> list[EmailMetadata]:
    """List messages and fetch only their useful metadata headers."""
    service = get_gmail_service()
    request = service.users().messages().list(
        userId="me",
        maxResults=_bounded_max_results(max_results),
        **({"q": query} if query else {}),
    )
    messages = request.execute().get("messages", [])

    results: list[EmailMetadata] = []
    for message in messages:
        if not isinstance(message, dict) or not message.get("id"):
            continue
        details = service.users().messages().get(
            userId="me",
            id=message["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        results.append(_email_metadata(details))
    return results


def _list_error(message: str) -> list[dict[str, str]]:
    """Keep list-tool errors useful without exposing exception details."""
    return [{"error": message}]


@mcp.tool()
def search_emails(query: str, max_results: int = 10) -> list[EmailMetadata] | list[dict[str, str]]:
    """Search matching Gmail messages by sender, subject, topic, or Gmail search query.

    Use for targeted searches. Returns sender, subject, date, snippet, and message ID.
    """
    try:
        return _list_emails(query, max_results)
    except GmailAuthenticationError:
        return _list_error("Gmail authentication is unavailable.")
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return _list_error("Gmail API request failed.")
    except Exception:
        return _list_error("Gmail API request failed.")


@mcp.tool()
def get_email(message_id: str) -> EmailMessage | dict[str, str]:
    """Retrieve one message's metadata and readable body when its content is needed."""
    if not message_id.strip():
        return {"error": "Gmail message not found."}

    try:
        service = get_gmail_service()
        message = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        result: EmailMessage = _email_metadata(message)
        result["body"] = _message_body(message.get("payload"))
        return result
    except GmailAuthenticationError:
        return {"error": "Gmail authentication is unavailable."}
    except HttpError as error:
        if error.resp.status == 404:
            return {"error": "Gmail message not found."}
        return {"error": "Gmail API request failed."}
    except (GoogleAuthError, OSError, ValueError):
        return {"error": "Gmail API request failed."}
    except Exception:
        return {"error": "Gmail API request failed."}


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> dict[str, str]:
    """Send one email through Gmail; the host calls this only after explicit confirmation."""
    recipient = to.strip()
    if (
        len(recipient) > 254
        or not _EMAIL_ADDRESS.fullmatch(recipient)
        or recipient.split("@", maxsplit=1)[0].startswith(".")
        or recipient.split("@", maxsplit=1)[0].endswith(".")
        or ".." in recipient.split("@", maxsplit=1)[0]
    ):
        return {"error": "A valid single recipient email address is required."}
    if not subject.strip() or len(subject) > 998 or "\r" in subject or "\n" in subject:
        return {"error": "A non-empty subject without line breaks is required."}
    if not body.strip() or len(body) > 100_000:
        return {"error": "A non-empty email body within the size limit is required."}

    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        sender = str(profile.get("emailAddress", ""))
        if not sender:
            return {"error": "Gmail could not identify the sending account."}

        message = MIMEEmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = subject.strip()
        message.set_content(body)
        raw_message = base64.urlsafe_b64encode(
            message.as_bytes(policy=SMTP)
        ).decode("ascii").rstrip("=")
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw_message},
        ).execute()
        return {"status": "sent", "message_id": str(sent.get("id", ""))}
    except GmailAuthenticationError:
        return {"error": "Gmail authorization is unavailable; reauthorize the Gmail account."}
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return {"error": "Gmail could not send the email."}
    except Exception:
        return {"error": "Gmail could not send the email."}


@mcp.tool()
def list_recent_emails(max_results: int = 10) -> list[EmailMetadata] | list[dict[str, str]]:
    """List the newest Gmail messages when the user asks for recent or latest emails.

    Returns message metadata and preview snippets, up to 50 messages.
    """
    try:
        return _list_emails(None, max_results)
    except GmailAuthenticationError:
        return _list_error("Gmail authentication is unavailable.")
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return _list_error("Gmail API request failed.")
    except Exception:
        return _list_error("Gmail API request failed.")


@mcp.tool()
def get_unread_emails(max_results: int = 10) -> list[EmailMetadata] | list[dict[str, str]]:
    """List unread Gmail messages when the user asks to see unread messages.

    Use count_emails instead when the user asks only for an unread count.
    """
    try:
        return _list_emails("is:unread", max_results)
    except GmailAuthenticationError:
        return _list_error("Gmail authentication is unavailable.")
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return _list_error("Gmail API request failed.")
    except Exception:
        return _list_error("Gmail API request failed.")


@mcp.tool()
def get_email_labels() -> list[GmailLabel] | dict[str, str]:
    """List the user's Gmail labels when the request is about labels or folders."""
    try:
        response = get_gmail_service().users().labels().list(userId="me").execute()
        labels = response.get("labels", [])
        return [
            {
                "id": str(label.get("id", "")),
                "name": str(label.get("name", "")),
                "type": str(label.get("type", "")),
            }
            for label in labels
            if isinstance(label, dict)
        ]
    except GmailAuthenticationError:
        return {"error": "Gmail authentication is unavailable."}
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return {"error": "Gmail API request failed."}
    except Exception:
        return {"error": "Gmail API request failed."}


@mcp.tool()
def count_emails(query: str = "") -> int | dict[str, str]:
    """Count messages matching an optional Gmail search query.

    Use query 'is:unread' for unread counts; returns an exact integer count.
    """
    query = query.strip()

    try:
        service = get_gmail_service()
        total = 0
        page_token: str | None = None
        while True:
            page = service.users().messages().list(
                userId="me",
                maxResults=500,
                **({"q": query} if query else {}),
                **({"pageToken": page_token} if page_token else {}),
            ).execute()
            total += len(page.get("messages", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return total
    except GmailAuthenticationError:
        return {"error": "Gmail authentication is unavailable."}
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return {"error": "Gmail API request failed."}
    except Exception:
        return {"error": "Gmail API request failed."}


@mcp.tool()
def search_emails_by_date(
    start_date: str,
    end_date: str,
    query: str = "",
    max_results: int = 10,
) -> list[EmailMetadata] | list[dict[str, str]]:
    """Search message metadata in an inclusive date range.

    Use for requests about messages from or within dates. Dates must use YYYY-MM-DD.
    An optional Gmail query can further filter the range.
    """
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < start:
            return _list_error("The end date must be the same as or later than the start date.")
        gmail_query = (
            f"after:{start:%Y/%m/%d} "
            f"before:{(end + timedelta(days=1)):%Y/%m/%d}"
        )
    except (ValueError, OverflowError):
        return _list_error("Dates must use YYYY-MM-DD, with a valid start and end date.")

    full_query = f"{query.strip()} {gmail_query}".strip()
    try:
        return _list_emails(full_query, max_results)
    except GmailAuthenticationError:
        return _list_error("Gmail authentication is unavailable.")
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return _list_error("Gmail API request failed.")
    except Exception:
        return _list_error("Gmail API request failed.")


@mcp.tool()
def get_email_summary_data(
    query: str = "",
    max_results: int = 10,
) -> list[EmailMetadata] | list[dict[str, str]]:
    """Retrieve recent matching metadata and snippets for analysis or summarization.

    Use when a summary can be based on message headers and preview snippets; does not
    download full message bodies.
    """
    try:
        return _list_emails(query.strip() or None, max_results)
    except GmailAuthenticationError:
        return _list_error("Gmail authentication is unavailable.")
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return _list_error("Gmail API request failed.")
    except Exception:
        return _list_error("Gmail API request failed.")


@mcp.resource(
    "gmail://account",
    name="Gmail Account Information",
    description="Read-only account email, Gmail API scope, and this MCP server's name.",
    mime_type="application/json",
)
def gmail_account_information() -> str:
    """Expose basic account information without returning credentials or message data."""
    try:
        profile = get_gmail_service().users().getProfile(userId="me").execute()
        return json.dumps(
            {
                "account_email": str(profile.get("emailAddress", "")),
                "gmail_api_scope": SCOPES[0],
                "server_name": SERVER_NAME,
            }
        )
    except GmailAuthenticationError:
        return json.dumps({"error": "Gmail authentication is unavailable."})
    except (GoogleAuthError, HttpError, OSError, ValueError):
        return json.dumps({"error": "Gmail API request failed."})
    except Exception:
        return json.dumps({"error": "Gmail API request failed."})


@mcp.prompt(
    description="Guide a read-only Gmail search using the appropriate available tool."
)
def search_gmail(request: str) -> list[dict[str, str]]:
    """Turn a natural-language Gmail request into tool-selection guidance."""
    return [
        {
            "role": "user",
            "content": (
                "Help with this Gmail request using only the available read-only tools.\n"
                f"Request: {request}\n\n"
                "Choose the appropriate tool: use get_unread_emails for unread messages, "
                "count_emails for a requested count, search_emails_by_date for a date range, "
                "search_emails for sender/subject/topic searches, list_recent_emails for the latest "
                "messages, get_email for a supplied message ID, or get_email_summary_data for "
                "metadata-only summaries. Base the answer on the tool result. Do not send, delete, "
                "or modify email."
            ),
        }
    ]


if __name__ == "__main__":
    mcp.run()