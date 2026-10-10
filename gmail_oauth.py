import json
import os

from cryptography.fernet import Fernet, InvalidToken
from google_auth_oauthlib.flow import Flow

from gmail_mcp_server import SCOPES


def _client_config() -> dict[str, object]:
    raw_config = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "")
    try:
        config = json.loads(raw_config)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON must contain a Google Web OAuth client JSON object.") from None
    web_config = config.get("web") if isinstance(config, dict) else None
    if not isinstance(web_config, dict):
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON must be a Google Web OAuth client, not a Desktop client.")
    required_keys = {"client_id", "client_secret", "auth_uri", "token_uri"}
    if not required_keys.issubset(web_config):
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON is missing required Web OAuth client fields.")
    return config


def _redirect_uri() -> str:
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if not redirect_uri.startswith("https://"):
        raise RuntimeError("GOOGLE_OAUTH_REDIRECT_URI must be the HTTPS production callback URL.")
    return redirect_uri


def authorization_url(state: str) -> str:
    try:
        flow = Flow.from_client_config(
            _client_config(),
            scopes=SCOPES,
            state=state,
            redirect_uri=_redirect_uri(),
        )
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return url
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Could not start Gmail OAuth. Check the Web OAuth configuration.") from None


def exchange_code(code: str, state: str) -> str:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=_redirect_uri(),
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    if not credentials.has_scopes(SCOPES):
        raise RuntimeError("Gmail authorization did not grant the required read and send scopes.")
    return encrypt_credentials(credentials.to_json())


def encrypt_credentials(credentials_json: str) -> str:
    key = os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY", "").encode("ascii")
    try:
        return Fernet(key).encrypt(credentials_json.encode("utf-8")).decode("ascii")
    except (ValueError, UnicodeEncodeError):
        raise RuntimeError("GMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from None


def decrypt_credentials(ciphertext: str) -> str:
    key = os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY", "").encode("ascii")
    try:
        return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError):
        raise RuntimeError("Stored Gmail authorization could not be decrypted.") from None