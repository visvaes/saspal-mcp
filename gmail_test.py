from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"


def get_credentials() -> Credentials:
    """Load, refresh, or request Gmail authorization."""
    if not CREDENTIALS_FILE.is_file():
        raise FileNotFoundError(
            "credentials.json was not found. Place your downloaded Desktop OAuth "
            "client JSON file in the project root and run this script again."
        )

    creds = None
    if TOKEN_FILE.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except (OSError, ValueError):
            TOKEN_FILE.unlink(missing_ok=True)
            creds = None

    if creds and creds.expired and creds.refresh_token and creds.has_scopes(SCOPES):
        try:
            creds.refresh(Request())
        except Exception:
            TOKEN_FILE.unlink(missing_ok=True)
            creds = None

    if not creds or not creds.valid or not creds.has_scopes(SCOPES):
        if TOKEN_FILE.is_file():
            TOKEN_FILE.unlink(missing_ok=True)
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def main() -> None:
    try:
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds)
        result = service.users().messages().list(userId="me", maxResults=5).execute()
        messages = result.get("messages", [])

        print("Gmail API authentication succeeded.")
        if not messages:
            print("No Gmail messages were found.")
            return

        print("Latest message IDs:")
        for message in messages:
            print(message["id"])
    except FileNotFoundError as error:
        print(f"Setup error: {error}")
    except HttpError as error:
        print(f"Gmail API error: {error}")
    except (GoogleAuthError, OSError, ValueError) as error:
        print(f"Authentication error: {error}")
    except Exception as error:
        print(f"Unexpected error: {error}")


if __name__ == "__main__":
    main()