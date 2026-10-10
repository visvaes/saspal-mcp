import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from pwdlib import PasswordHash

import auth_mailer
import auth_store
from mcp_host import MCPChatHost


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
logger = logging.getLogger(__name__)
SESSION_COOKIE = "saspal_session"
CSRF_COOKIE = "saspal_csrf"
SESSION_COOKIE_SECURE = os.getenv("VERCEL") == "1" or os.getenv(
    "SESSION_COOKIE_SECURE", "false"
).casefold() == "true"
TRUSTED_ORIGINS = {
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
}
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-account-password")


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=12_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=24)


class ChatResponse(BaseModel):
    response: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    max_results: int = Field(default=10, ge=1, le=50)


class DateSearchRequest(BaseModel):
    start_date: str = Field(min_length=1, max_length=20)
    end_date: str = Field(min_length=1, max_length=20)
    query: str = Field(default="", max_length=2_000)
    max_results: int = Field(default=10, ge=1, le=50)


class EmailDraftRequest(BaseModel):
    to: str = Field(min_length=3, max_length=254)
    instructions: str = Field(default="", max_length=8_000)
    subject: str | None = Field(default=None, max_length=998)
    body: str | None = Field(default=None, max_length=100_000)


class EmailDraftActionRequest(BaseModel):
    draft_id: str = Field(min_length=32, max_length=128)


class SignUpRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Full name is required.")
        return value

    @model_validator(mode="after")
    def passwords_match(self) -> "SignUpRequest":
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.password != self.password_confirmation:
            raise ValueError("Passwords do not match.")
        return self


def _public_user(session: dict[str, object]) -> dict[str, object]:
    return {
        "id": session["id"],
        "full_name": session["full_name"],
        "email": session["email"],
    }


def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=auth_store.SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=auth_store.SESSION_TTL_SECONDS,
        httponly=False,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=SESSION_COOKIE_SECURE, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=SESSION_COOKIE_SECURE, httponly=False, samesite="strict")


def _current_session(request: Request) -> dict[str, object] | None:
    session_token = request.cookies.get(SESSION_COOKIE)
    return auth_store.get_session(session_token) if session_token else None


def _origin_is_allowed(request: Request, origin: str) -> bool:
    if origin in TRUSTED_ORIGINS:
        return True
    host = request.headers.get("host")
    return bool(host and origin == f"{request.url.scheme}://{host}")


async def require_authenticated_user(request: Request) -> AsyncIterator[dict[str, object]]:
    session = _current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = None
    if os.getenv("VERCEL") == "1":
        import gmail_mcp_server

        token = gmail_mcp_server.set_current_gmail_user_id(int(session["id"]))
    try:
        yield session
    finally:
        if token is not None:
            gmail_mcp_server.reset_current_gmail_user_id(token)


def gmail_is_connected(user_id: int | None = None) -> bool:
    try:
        import gmail_mcp_server

        if os.getenv("VERCEL") == "1":
            if user_id is None:
                return False
            gmail_mcp_server.get_gmail_service.cache_clear()
            gmail_mcp_server.get_gmail_service(user_id)
        else:
            gmail_mcp_server.get_gmail_service()
        return True
    except Exception:
        return False


def connect_gmail() -> bool:
    try:
        from gmail_test import get_credentials

        get_credentials()
        return gmail_is_connected()
    except Exception as error:
        raise RuntimeError("Gmail connection failed. Please try again.") from error


def _with_csrf_cookie(response: Response, request: Request) -> Response:
    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(32),
            httponly=False,
            secure=SESSION_COOKIE_SECURE,
            samesite="strict",
            path="/",
        )
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth_store.initialize_auth_database()
    host = MCPChatHost()
    try:
        await host.start()
    except Exception as error:
        await host.close()
        raise RuntimeError(
            f"Could not start the Gmail MCP host ({type(error).__name__})."
        ) from None

    app.state.chat_host = host
    try:
        yield
    finally:
        await host.close()


app = FastAPI(title="SASPAL Gmail AI Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(TRUSTED_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.middleware("http")
async def enforce_csrf(request: Request, call_next):
    if request.method == "GET" and request.url.path == "/static/index.html":
        if _current_session(request) is None:
            return RedirectResponse("/login?next=/", status_code=303)

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and not _origin_is_allowed(request, origin):
            return JSONResponse(status_code=403, content={"detail": "Request origin is not allowed."})

        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed."})

        session = _current_session(request)
        if session is not None:
            expected_hash = hashlib.sha256(csrf_cookie.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(expected_hash, str(session["csrf_hash"])):
                return JSONResponse(status_code=403, content={"detail": "CSRF validation failed."})

    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def sanitize_auth_validation_errors(request: Request, error: RequestValidationError):
    if request.url.path.startswith("/auth/"):
        return JSONResponse(status_code=422, content={"detail": "Invalid authentication request."})
    return await request_validation_exception_handler(request, error)


def _auth_page(request: Request, filename: str) -> Response:
    if _current_session(request) is not None:
        return RedirectResponse("/", status_code=303)
    return _with_csrf_cookie(FileResponse(FRONTEND_DIR / filename), request)


@app.get("/login", include_in_schema=False)
async def login_page(request: Request) -> Response:
    return _auth_page(request, "login.html")


@app.get("/signup", include_in_schema=False)
async def signup_page(request: Request) -> Response:
    return _auth_page(request, "signup.html")


@app.get("/forgot-password", include_in_schema=False)
async def forgot_password_page(request: Request) -> Response:
    return _auth_page(request, "forgot-password.html")


@app.get("/reset-password", include_in_schema=False)
async def reset_password_page(request: Request) -> Response:
    return _auth_page(request, "reset-password.html")


@app.get("/", include_in_schema=False)
async def index(request: Request) -> Response:
    if _current_session(request) is None:
        return RedirectResponse("/login?next=/", status_code=303)
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/connect-gmail", include_in_schema=False)
async def connect_gmail_page(request: Request) -> Response:
    if _current_session(request) is None:
        return RedirectResponse("/login?next=/connect-gmail", status_code=303)
    return FileResponse(FRONTEND_DIR / "connect-gmail.html")


@app.get("/api/gmail-status")
async def gmail_status(user: dict[str, object] = Depends(require_authenticated_user)) -> dict[str, bool]:
    return {"connected": gmail_is_connected(int(user["id"]))}


@app.post("/api/gmail/connect")
async def gmail_connect(
    request: Request,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, bool | str]:
    if os.getenv("VERCEL") == "1":
        try:
            import gmail_oauth

            state = auth_store.create_oauth_state(int(user["id"]))
            return {"authorization_url": gmail_oauth.authorization_url(state)}
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
    try:
        return {"connected": connect_gmail()}
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.get("/api/gmail/oauth/callback", include_in_schema=False)
async def gmail_oauth_callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
    user = _current_session(request)
    if user is None:
        return RedirectResponse("/login?next=/connect-gmail", status_code=303)
    if error or not state or not code:
        return RedirectResponse("/connect-gmail?error=oauth", status_code=303)

    user_id = auth_store.consume_oauth_state(state)
    if user_id is None or user_id != int(user["id"]):
        return RedirectResponse("/connect-gmail?error=oauth", status_code=303)

    try:
        import gmail_mcp_server
        import gmail_oauth

        encrypted_credentials = gmail_oauth.exchange_code(code, state)
        auth_store.store_gmail_credentials(user_id, encrypted_credentials)
        gmail_mcp_server.get_gmail_service.cache_clear()
    except Exception:
        logger.warning("Gmail OAuth callback failed.")
        return RedirectResponse("/connect-gmail?error=oauth", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/signup")
async def sign_up(request: SignUpRequest, response: Response) -> dict[str, object]:
    password_hash = password_hasher.hash(request.password)
    try:
        user = auth_store.create_user(request.full_name, str(request.email), password_hash)
    except Exception as error:
        if not auth_store.is_duplicate_email_error(error):
            raise
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from None

    session_token, csrf_token = auth_store.create_session(int(user["id"]))
    _set_auth_cookies(response, session_token, csrf_token)
    return {"user": user}


@app.post("/auth/login")
async def sign_in(request: LoginRequest, response: Response) -> dict[str, object]:
    user = auth_store.get_user_by_email(str(request.email))
    stored_hash = str(user["password_hash"]) if user else DUMMY_PASSWORD_HASH
    try:
        password_matches = password_hasher.verify(request.password, stored_hash)
    except Exception:
        password_matches = False

    if user is None or not user["is_active"] or not password_matches:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    session_token, csrf_token = auth_store.create_session(int(user["id"]))
    _set_auth_cookies(response, session_token, csrf_token)
    return {
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
        }
    }


@app.get("/auth/me")
async def current_user(user: dict[str, object] = Depends(require_authenticated_user)) -> dict[str, object]:
    return _public_user(user)


@app.post("/auth/logout")
async def sign_out(
    request: Request,
    response: Response,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token:
        auth_store.revoke_session(session_token)
    _clear_auth_cookies(response)
    return {"logged_out": True}


@app.post("/auth/forgot-password")
async def forgot_password(request: ForgotPasswordRequest) -> dict[str, str]:
    if not auth_mailer.is_configured():
        raise HTTPException(status_code=503, detail="Password recovery is not configured.")

    user = auth_store.get_user_by_email(str(request.email))
    if user and user["is_active"]:
        token = auth_store.create_password_reset_token(int(user["id"]))
        try:
            auth_mailer.send_password_reset_email(str(user["email"]), token)
        except Exception:
            auth_store.delete_password_reset_token(token)
            logger.warning("Password recovery email delivery failed.")
    return {"message": "If an account matches that address, a reset link will be sent."}


@app.post("/auth/reset-password")
async def reset_password(request: ResetPasswordRequest) -> dict[str, str]:
    updated = auth_store.reset_password(request.token, password_hasher.hash(request.password))
    if not updated:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired.")
    return {"message": "Password updated. Sign in with your new password."}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> ChatResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message must not be empty.")

    history = [turn.model_dump() for turn in request.history]
    try:
        response = await app.state.chat_host.respond(message, history)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Chat request failed ({type(error).__name__}). Check backend connectivity.",
        ) from None
    return ChatResponse(response=response)


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> StreamingResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message must not be empty.")

    history = [turn.model_dump() for turn in request.history]

    async def stream_events():
        events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        def report_status(status: str) -> None:
            events.put_nowait(("status", {"status": status}))

        def report_emails(emails: list[dict[str, object]]) -> None:
            events.put_nowait(("emails", {"emails": emails}))

        task = asyncio.create_task(
            app.state.chat_host.respond(
                message,
                history,
                status_callback=report_status,
                email_results_callback=report_emails,
            )
        )
        try:
            while not task.done() or not events.empty():
                try:
                    event, data = await asyncio.wait_for(events.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            try:
                response = task.result()
            except Exception as error:
                logger.warning("Chat request failed (%s).", type(error).__name__)
                yield (
                    'event: error\ndata: {"message":"I could not complete that request. '
                    'Please try again."}\n\n'
                )
                return
            yield (
                "event: done\ndata: "
                + json.dumps({"response": response}, ensure_ascii=False)
                + "\n\n"
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/recent-emails")
async def recent_emails(
    user: dict[str, object] = Depends(require_authenticated_user),
) -> list[dict[str, object]]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to use email features.")
    try:
        return await app.state.chat_host.get_recent_emails(max_results=10)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Recent emails request failed ({type(error).__name__}).",
        ) from None


@app.get("/api/unread-count")
async def unread_count(
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, int]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to use email features.")
    try:
        return {"count": await app.state.chat_host.get_unread_count()}
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Unread count request failed ({type(error).__name__}).",
        ) from None


@app.post("/api/search")
async def search_emails(
    request: SearchRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> list[dict[str, object]]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to use email features.")
    try:
        return await app.state.chat_host.search_emails(request.query, request.max_results)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Email search failed ({type(error).__name__}).",
        ) from None


@app.post("/api/date-search")
async def date_search(
    request: DateSearchRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> list[dict[str, object]]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to use email features.")
    try:
        return await app.state.chat_host.search_by_date(
            request.start_date,
            request.end_date,
            request.query,
            request.max_results,
        )
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Date search failed ({type(error).__name__}).",
        ) from None


@app.post("/api/email-drafts")
async def create_email_draft(
    request: EmailDraftRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, str]:
    try:
        return await app.state.chat_host.prepare_email_draft(
            request.to,
            request.instructions,
            subject=request.subject,
            body=request.body,
            user_id=int(user["id"]),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.post("/api/email-drafts/confirm")
async def confirm_email_draft(
    request: EmailDraftActionRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, str]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to send email.")
    try:
        return await app.state.chat_host.confirm_email_draft(
            request.draft_id,
            user_id=int(user["id"]),
        )
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.post("/api/email-drafts/cancel")
async def cancel_email_draft(
    request: EmailDraftActionRequest,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, bool]:
    cancelled = await app.state.chat_host.cancel_email_draft(
        request.draft_id,
        user_id=int(user["id"]),
    )
    return {"cancelled": cancelled}


@app.get("/api/email/{message_id}")
async def open_email(
    message_id: str,
    user: dict[str, object] = Depends(require_authenticated_user),
) -> dict[str, object]:
    if not gmail_is_connected(int(user["id"])):
        raise HTTPException(status_code=403, detail="Connect Gmail to use email features.")
    if not message_id.strip():
        raise HTTPException(status_code=400, detail="Email message ID is required.")

    try:
        email = await app.state.chat_host.get_email(message_id)
        if "error" in email:
            raise HTTPException(status_code=404, detail=str(email["error"]))
        return email
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Email lookup failed ({type(error).__name__}).",
        ) from None
