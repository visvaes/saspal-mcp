# SASPAL Gmail MCP

SASPAL Gmail MCP is a local web assistant for searching and reading Gmail, summarizing email metadata, and preparing messages for review. Sending is a separate action that requires the user to confirm the exact draft.

The current MCP host also starts the read-only SASPAL Technologies MCP, which reads company information from the local SQLite database. Gmail remains the purpose of this guide; the database and SASPAL MCP are existing supporting project components.

## Architecture

```text
User
 ↓
Web UI
 ↓
FastAPI
 ↓
MCP Host
 ↓
Gmail MCP Server
 ↓
Gmail API
```

The MCP Host also connects to the SASPAL Technologies MCP for its existing read-only company-information tools.

## Conversational agent behavior

Chat requests go to the LLM with the discovered MCP tool descriptions. The LLM answers greetings and general
questions directly, and selects Gmail tools only when the request needs mailbox data. Tool results are returned
to the LLM for analysis, with a five-call limit per request. Recent conversation context is bounded to 24 turns
and sensitive values are redacted before it is reused. The chat UI streams safe progress labels and sanitized
email records; it does not display prompts, tool arguments, or hidden reasoning.

## Gmail features

- Search email using Gmail search syntax
- Read individual messages and their readable text body
- List recent and unread emails
- Search by inclusive date range
- Summarize recent email metadata and snippets
- Compose a draft from a user request
- Review the recipient, subject, and message before sending
- Confirm and send a reviewed draft, or cancel it

## Security model

- Account passwords are hashed with Argon2id. Opaque sessions are stored server-side in a separate local auth database; only session-token hashes are persisted.
- Session cookies are HttpOnly and SameSite-protected. State-changing requests require CSRF validation. Set `SESSION_COOKIE_SECURE=true` when serving over HTTPS.
- Password reset uses short-lived, single-use tokens stored as hashes and sent through separately configured SMTP. It does not use Gmail OAuth.
- All app accounts use the project's existing app-wide Gmail OAuth account. Every authenticated account can access the same configured mailbox.
- Gmail access uses OAuth 2.0 credentials stored locally.
- The requested Gmail scopes are `gmail.readonly` and `gmail.send`; `gmail.modify` is not requested.
- The LLM cannot access the `send_email` tool during normal chat. The application only calls it after the user explicitly confirms a pending draft.
- Draft confirmation identifiers are short-lived and single-use. Cancellation consumes the pending draft without sending.
- Email content is untrusted input. It must not override system instructions or authorize sending.
- Sensitive-data redaction is applied to email-derived content, including common OTP and credential-like values. Redaction is a defense-in-depth measure, not a guarantee that all sensitive content is detected.
- OAuth files, environment files, and the local database are excluded by `.gitignore`. Keep them out of commits, archives, logs, screenshots, and public issue reports.

## Installation requirements

- Python 3.10 or later
- Node.js 18 or later (only needed for the frontend parser tests)
- A Google Cloud project with the Gmail API enabled
- A Google Desktop OAuth client JSON file for local authorization
- An OpenRouter API key for LLM-powered chat and draft generation

Install Python dependencies from the project root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For PowerShell sessions where script activation is restricted, invoke `.venv\Scripts\python.exe` directly instead.

## Environment setup

Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` to your own key. `OPENROUTER_MODEL` is optional; the application uses its configured default when it is omitted. Configure the SMTP fields to enable password recovery. Never commit `.env` or share its contents.

```powershell
Copy-Item .env.example .env
```

## Gmail OAuth setup

1. In Google Cloud Console, select or create a project, enable the Gmail API, and configure the OAuth consent screen as required for your account.
2. Create an OAuth client with the **Desktop app** application type and download its client JSON.
3. Save the downloaded file as `credentials.json` in the project root. This file contains private client credentials and must remain local.
4. With the virtual environment active, run:

   ```powershell
   python gmail_test.py
   ```

5. Complete the browser authorization. The helper saves the resulting OAuth token as `token.json` in the project root. Keep it private; do not print or share it.

The OAuth helper checks Gmail authorization and lists message IDs only. It does not send email. Reauthorize through the helper if the saved authorization becomes invalid.

## Account access and password recovery

Open `/signup` to create an account. A successful sign-up signs the account in and opens the assistant. The assistant and its Gmail/chat APIs require an active session; use **Sign Out** to revoke the current session.

All accounts share the single Gmail mailbox authorized by the local OAuth token. Do not enable open registration on a public deployment unless every registrant is trusted to access that mailbox. Add an invitation or account-approval policy before exposing registration beyond a trusted local environment.

The Forgot Password flow requires SMTP settings in `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_EMAIL`, `APP_BASE_URL`, and optionally `SMTP_USERNAME`/`SMTP_PASSWORD`. Reset links expire after 30 minutes and can be used once. The application returns a generic message to avoid confirming whether an email is registered. No SMTP password or reset token belongs in Git.

## Start the application

From the project root, with `.env`, `credentials.json`, and a valid `token.json` in place:

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The host binds to loopback for local use. Do not expose it to a network or deploy it publicly as-is.

Vercel's FastAPI deployment entrypoint is set explicitly to `web_app:app` in `pyproject.toml`, so test modules that import the app are not mistaken for the deployment target. That file also declares the Python version and runtime dependencies required by Vercel's `uv` build. This resolves entrypoint and dependency-manifest detection only; the current application also depends on local OAuth files, SQLite persistence, and subprocess-hosted MCP servers. Vercel deployment requires replacing those local-only dependencies with deployment-compatible secrets, persistent storage, and MCP connectivity before the application can operate there.

## Run tests

Run Python tests and syntax validation:

```powershell
.\.venv\Scripts\python.exe -m py_compile gmail_mcp_server.py web_app.py mcp_host.py tests/test_gmail_mcp.py tests/test_agent_behavior.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest tests.test_auth -v
.\.venv\Scripts\python.exe -m unittest test_gmail_auth -v
```

Run JavaScript syntax checks and frontend parser tests:

```powershell
node --check frontend/email-intent.js
node --check frontend/app.js
node --check frontend/auth.js
node --test frontend/email-intent.test.cjs
```

The automated send tests use mocked Gmail clients; they do not send real email. Do not run manual send actions as part of testing.

## Example user requests

- “Show me my latest emails.”
- “Find unread messages about invoices.”
- “Search for emails from last week about the project.”
- “Summarize my latest five emails.”
- “Open the message with ID `<message-id>`.”
- “Draft an email to `<recipient>` about rescheduling the meeting.”

A draft must be reviewed and explicitly confirmed in the UI before it can be sent.

## Project structure

```text
.
|-- .env.example                 # Safe placeholder template; copy to local .env
|-- .gitignore
|-- README.md
|-- requirements.txt
|-- auth_mailer.py
|-- auth_store.py
|-- database.py                  # Existing local company-information database access
|-- gmail_mcp_server.py          # Gmail MCP tools and OAuth service
|-- gmail_test.py                # Local OAuth setup/check helper
|-- mcp_host.py                  # MCP orchestration and draft confirmation gate
|-- saspal_mcp_server.py         # Existing read-only company-information MCP
|-- test_gmail_auth.py           # Additional OAuth error-message test
|-- web_app.py                   # FastAPI routes and application lifecycle
|-- tests/
|   |-- test_gmail_mcp.py        # Gmail-focused automated tests
|-- frontend/
    |-- auth.css
    |-- auth.js
    |-- login.html
    |-- signup.html
    |-- forgot-password.html
    |-- reset-password.html
    |-- app.js
    |-- email-intent.js
    |-- email-intent.test.cjs
    |-- index.html
    |-- styles.css
```

Local-only files may also exist: `.env`, `credentials.json`, `token.json`, `token.json.bak`, `saspal.db`, `auth.sqlite3`, `.venv/`, and `__pycache__/`. They are not project source and must not be added to Git.

## Important security warnings

- Never commit or publish `credentials.json`, `token.json`, `token.json.bak`, `.env`, or any copied OAuth/API secret.
- Treat `auth.sqlite3` as private user data. It contains account records, password hashes, and session/reset-token hashes.
- If a secret is accidentally committed, removing it in a later commit is not sufficient. Revoke or rotate it and clean the repository history.
- This application has no production authentication layer for web users. Loopback binding limits local exposure but is not a production deployment security boundary.
- Before deployment, add appropriate user authentication, HTTPS termination, CSRF/origin protections, secret management, access controls, and operational monitoring. Do not expose the current development server directly to the internet.
- Email bodies and prompts can contain malicious instructions. Treat email content as untrusted and verify the complete recipient and message before confirming a send.
- Never use real sending as a test. Automated tests must keep Gmail API calls mocked.
