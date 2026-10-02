from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mcp_host import MCPChatHost


PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
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


@app.get("/api/recent-emails")
async def recent_emails() -> list[dict[str, object]]:
    try:
        return await app.state.chat_host.get_recent_emails(max_results=10)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Recent emails request failed ({type(error).__name__}).",
        ) from None


@app.get("/api/unread-count")
async def unread_count() -> dict[str, int]:
    try:
        return {"count": await app.state.chat_host.get_unread_count()}
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Unread count request failed ({type(error).__name__}).",
        ) from None


@app.post("/api/search")
async def search_emails(request: SearchRequest) -> list[dict[str, object]]:
    try:
        return await app.state.chat_host.search_emails(request.query, request.max_results)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Email search failed ({type(error).__name__}).",
        ) from None


@app.post("/api/date-search")
async def date_search(request: DateSearchRequest) -> list[dict[str, object]]:
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
async def create_email_draft(request: EmailDraftRequest) -> dict[str, str]:
    try:
        return await app.state.chat_host.prepare_email_draft(
            request.to,
            request.instructions,
            subject=request.subject,
            body=request.body,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.post("/api/email-drafts/confirm")
async def confirm_email_draft(request: EmailDraftActionRequest) -> dict[str, str]:
    try:
        return await app.state.chat_host.confirm_email_draft(request.draft_id)
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.post("/api/email-drafts/cancel")
async def cancel_email_draft(request: EmailDraftActionRequest) -> dict[str, bool]:
    cancelled = await app.state.chat_host.cancel_email_draft(request.draft_id)
    return {"cancelled": cancelled}


@app.get("/api/email/{message_id}")
async def open_email(message_id: str) -> dict[str, object]:
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
