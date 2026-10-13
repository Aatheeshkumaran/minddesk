import json
import logging
import os
import secrets
import sqlite3
import hashlib
import hmac
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"
DB_PATH = PROJECT_ROOT / "minddesk.db"
# 127.0.0.1 instead of "localhost": avoids slow IPv6 (::1) lookups on Windows
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "").strip()
KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")        # keep model loaded between messages
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))        # sane context window
MAX_HISTORY = int(os.getenv("MINDDESK_MAX_HISTORY", "20"))  # only send recent messages to the model
SESSION_COOKIE = "minddesk_session"

log = logging.getLogger("minddesk")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MindDesk API", version="2.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 180_000).hex()


def current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT users.id, users.name, users.email FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token=?",
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
                "INSERT INTO users (name, email, password_hash, salt) VALUES (?, ?, ?, ?)",
                (name, email, password_hash, salt),
            )
            user_id = cur.lastrowid
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please sign in.") from exc
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60*60*24*14)
    return {"ok": True, "user": {"id": user_id, "name": name, "email": email}}


@app.post("/api/login")
async def login(body: AuthRequest, response: Response):
    email = body.email.strip().lower()
    with closing(db()) as conn, conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not hmac.compare_digest(hash_password(body.password, user["salt"]), user["password_hash"]):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60*60*24*14)
    return {"ok": True, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with closing(db()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/health")
async def health(request: Request):
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()
        models = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
        return {"ok": True, "ollama": "connected", "models": models}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "ollama": "disconnected", "models": []}


@app.get("/api/models")
async def models(request: Request):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
        data = response.json()
        names = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
        return {"models": names, "default": DEFAULT_MODEL if DEFAULT_MODEL in names else (names[0] if names else "")}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Can't reach Ollama. Open Ollama and try again.") from exc


# ---------------------------------------------------------------- chat helpers
async def resolve_model(body: ChatRequest) -> str:
    chosen_model = (body.model or DEFAULT_MODEL).strip()
    if not chosen_model:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                tags = await client.get(f"{OLLAMA_URL}/api/tags")
                tags.raise_for_status()
            available = tags.json().get("models", [])
            chosen_model = available[0].get("name", "") if available else ""
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="Ollama isn't responding. Make sure it is running.") from exc
    if not chosen_model:
        raise HTTPException(status_code=400, detail="No Ollama models found. Run `ollama pull llama3.2` in a terminal first.")
    return chosen_model


def build_payload(body: ChatRequest, user: dict, model: str, stream: bool) -> dict:
    messages = [
        {"role": "system", "content": f"You are MindDesk, a thoughtful, practical AI assistant. The user's name is {user['name']}. Be friendly and clear. Use concise structure and examples when helpful. Do not claim to have tools or access you do not have."}
    ]
    recent = body.messages[-MAX_HISTORY:]
    messages.extend({"role": m.role, "content": m.content} for m in recent)
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_ctx": NUM_CTX},
    }


def ollama_error_detail(exc: httpx.HTTPStatusError) -> str:
    detail = "Ollama couldn't complete the request."
    try:
        detail = exc.response.json().get("error", detail)
    except ValueError:
        pass
    return detail


# ---------------------------------------------------------------- chat (original, non-streaming)
@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    user = current_user(request) or {"name": "Guest"}
    model = await resolve_model(body)
    payload = build_payload(body, user, model, stream=False)
    started = time.perf_counter()
    log.info("chat -> %s (%d msgs)", model, len(payload["messages"]))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        answer = data.get("message", {}).get("content", "").strip()
        if not answer:
            raise HTTPException(status_code=502, detail="Ollama returned an empty response. Try again.")
        log.info("chat <- %.1fs", time.perf_counter() - started)
        return {"reply": answer, "model": data.get("model", model)}
    except httpx.TimeoutException as exc:
        log.warning("chat timeout after %.1fs", time.perf_counter() - started)
        raise HTTPException(status_code=504, detail="The model took too long to respond. Try a smaller model or shorter prompt.") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=ollama_error_detail(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Couldn't connect to Ollama. Check that it is running.") from exc


# ---------------------------------------------------------------- chat (NEW: streaming, newline-delimited JSON)
# Each line is {"delta": "text"}  ...  then {"done": true, "model": "..."}  or {"error": "..."}
@app.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    user = current_user(request) or {"name": "Guest"}
    model = await resolve_model(body)
    payload = build_payload(body, user, model, stream=True)

    async def gen():
        started = time.perf_counter()
        first = True
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                    if resp.status_code >= 400:
                        raw = (await resp.aread()).decode(errors="ignore")
                        try:
                            msg = json.loads(raw).get("error", "Ollama couldn't complete the request.")
                        except ValueError:
                            msg = "Ollama couldn't complete the request."
                        yield json.dumps({"error": msg}) + "\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            yield json.dumps({"error": chunk["error"]}) + "\n"
                            return
                        text = chunk.get("message", {}).get("content", "")
                        if text:
                            if first:
                                log.info("stream first token after %.1fs", time.perf_counter() - started)
                                first = False
                            yield json.dumps({"delta": text}) + "\n"
                        if chunk.get("done"):
                            break
            log.info("stream done in %.1fs", time.perf_counter() - started)
            yield json.dumps({"done": True, "model": model}) + "\n"
        except httpx.TimeoutException:
            yield json.dumps({"error": "The model took too long to respond. Try a smaller model or shorter prompt."}) + "\n"
        except httpx.HTTPError:
            yield json.dumps({"error": "Couldn't connect to Ollama. Check that it is running."}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})