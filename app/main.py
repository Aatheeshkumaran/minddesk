import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"
DB_PATH = PROJECT_ROOT / "minddesk.db"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
MAX_HISTORY = int(os.getenv("MINDDESK_MAX_HISTORY", "20"))
SESSION_COOKIE = "minddesk_session"

# These model IDs are offered by Groq, but availability can change.
# The backend falls back to DEFAULT_MODEL if the frontend sends an old Ollama model ID.
GROQ_MODELS = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
]

log = logging.getLogger("minddesk")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MindDesk API", version="2.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Database and authentication
# ---------------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )"""
        )


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 180_000
    ).hex()


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    with closing(db()) as conn:
        row = conn.execute(
            """SELECT users.id, users.name, users.email
               FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token = ?""",
            (token,),
        ).fetchone()

    return dict(row) if row else None


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user


class AuthRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    model: str | None = None


@app.on_event("startup")
def startup():
    init_db()


# ---------------------------------------------------------------------------
# Frontend and account endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/me")
async def me(request: Request):
    user = current_user(request)
    return {"authenticated": bool(user), "user": user}


@app.post("/api/signup")
async def signup(body: AuthRequest, response: Response):
    name = (body.name or "").strip()
    email = body.email.strip().lower()

    if not name:
        raise HTTPException(status_code=422, detail="Please enter your name.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")

    salt = secrets.token_hex(16)
    password_hash = hash_password(body.password, salt)

    try:
        with closing(db()) as conn, conn:
            cur = conn.execute(
                """INSERT INTO users (name, email, password_hash, salt)
                   VALUES (?, ?, ?, ?)""",
                (name, email, password_hash, salt),
            )
            user_id = cur.lastrowid
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
                (token, user_id),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please sign in.",
        ) from exc

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
        secure=True,
    )
    return {"ok": True, "user": {"id": user_id, "name": name, "email": email}}


@app.post("/api/login")
async def login(body: AuthRequest, response: Response):
    email = body.email.strip().lower()

    with closing(db()) as conn, conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

        if not user or not hmac.compare_digest(
            hash_password(body.password, user["salt"]), user["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")

        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, user["id"]),
        )

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
        secure=True,
    )
    return {
        "ok": True,
        "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
    }


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with closing(db()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Groq helpers and model endpoints
# ---------------------------------------------------------------------------

def groq_headers():
    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is missing. Add it in the Render Environment settings.",
        )
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


def resolve_model(requested_model: str | None) -> str:
    # Ignore model names sent by an older frontend if they are Ollama model IDs.
    if requested_model in GROQ_MODELS:
        return requested_model
    if DEFAULT_MODEL in GROQ_MODELS:
        return DEFAULT_MODEL
    return GROQ_MODELS[0]


def build_payload(body: ChatRequest, user: dict, stream: bool) -> dict:
    model = resolve_model(body.model)
    messages = [
        {
            "role": "system",
            "content": (
                "You are MindDesk, a thoughtful, practical AI assistant. "
                f"The user's name is {user['name']}. "
                "Be friendly and clear. Use concise structure and examples when helpful. "
                "Do not claim to have tools or access you do not have."
            ),
        }
    ]
    recent = body.messages[-MAX_HISTORY:]
    messages.extend(
        {"role": message.role, "content": message.content} for message in recent
    )
    return {"model": model, "messages": messages, "stream": stream}


def groq_error_detail(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            return error.get("message", "Groq could not complete the request.")
        return str(error)
    except (ValueError, AttributeError):
        return "Groq could not complete the request."


@app.get("/api/health")
async def health():
    if not GROQ_API_KEY:
        return {"ok": False, "provider": "groq", "models": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{GROQ_URL}/models",
                headers=groq_headers(),
            )
            response.raise_for_status()
            available = response.json().get("data", [])
            available_ids = [item.get("id") for item in available]
            usable = [model for model in GROQ_MODELS if model in available_ids]
        return {"ok": True, "provider": "groq", "models": usable or available_ids}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "provider": "groq", "models": []}


@app.get("/api/models")
async def models():
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is not configured.")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{GROQ_URL}/models",
                headers=groq_headers(),
            )
            response.raise_for_status()
            data = response.json().get("data", [])
            available_ids = [item.get("id") for item in data if item.get("id")]
            available = [model for model in GROQ_MODELS if model in available_ids]
            if not available:
                available = available_ids
            default = DEFAULT_MODEL if DEFAULT_MODEL in available else (
                available[0] if available else ""
            )
            return {"models": available, "default": default}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502, detail=groq_error_detail(exc.response)
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Could not connect to Groq."
        ) from exc


# ---------------------------------------------------------------------------
# Non-streaming chat endpoint
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    user = current_user(request) or {"name": "Guest"}
    payload = build_payload(body, user, stream=False)
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0)
        ) as client:
            response = await client.post(
                f"{GROQ_URL}/chat/completions",
                headers=groq_headers(),
                json=payload,
            )

        if response.is_error:
            raise HTTPException(status_code=502, detail=groq_error_detail(response))

        data = response.json()
        choices = data.get("choices", [])
        answer = (
            choices[0].get("message", {}).get("content", "").strip()
            if choices else ""
        )
        if not answer:
            raise HTTPException(status_code=502, detail="Groq returned an empty response.")

        log.info("Groq chat completed in %.1fs", time.perf_counter() - started)
        return {"reply": answer, "model": data.get("model", payload["model"])}

    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504, detail="Groq took too long to respond. Please try again."
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Could not connect to Groq. Please try again."
        ) from exc


# ---------------------------------------------------------------------------
# Streaming chat endpoint: newline-delimited JSON for the existing frontend
# ---------------------------------------------------------------------------

@app.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    user = current_user(request) or {"name": "Guest"}
    payload = build_payload(body, user, stream=True)

    async def generate():
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0, read=120.0, write=15.0, pool=10.0
                )
            ) as client:
                async with client.stream(
                    "POST",
                    f"{GROQ_URL}/chat/completions",
                    headers=groq_headers(),
                    json=payload,
                ) as response:
                    if response.is_error:
                        raw = await response.aread()
                        error_response = httpx.Response(
                            response.status_code,
                            content=raw,
                            headers=response.headers,
                        )
                        yield json.dumps(
                            {"error": groq_error_detail(error_response)}
                        ) + "\n"
                        return

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue

                        data = line[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue

                        if chunk.get("error"):
                            yield json.dumps({"error": str(chunk["error"])}) + "\n"
                            return

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if isinstance(content, str) and content:
                            yield json.dumps({"delta": content}) + "\n"

            yield json.dumps(
                {"done": True, "model": payload["model"]}
            ) + "\n"

        except httpx.TimeoutException:
            yield json.dumps(
                {"error": "Groq took too long to respond. Please try again."}
            ) + "\n"
        except httpx.HTTPError:
            yield json.dumps(
                {"error": "Could not connect to Groq. Please try again."}
            ) + "\n"
        except HTTPException as exc:
            yield json.dumps({"error": exc.detail}) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
