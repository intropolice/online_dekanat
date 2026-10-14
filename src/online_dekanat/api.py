from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware import Middleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from online_dekanat.collaboration import CollaborationStore, session_secret
from online_dekanat.mail_delivery import (
    EmailDeliveryError,
    send_temporary_password,
)
from online_dekanat.pipeline import DeanOfficePipeline
from online_dekanat.ui import UI_HTML
from online_dekanat.secure_storage import EncryptedStorage
from online_dekanat.database import StudentDatabase

MAX_CHAT_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_CHAT_EXTENSIONS = {
    ".bmp", ".doc", ".docx", ".jpeg", ".jpg", ".pdf", ".png",
    ".ppt", ".pptx", ".tif", ".tiff", ".txt", ".webp", ".xls", ".xlsx",
}

app = FastAPI(
    title="Онлайн-деканат",
    version="0.1.0",
    middleware=[
        Middleware(
            SessionMiddleware,
            secret_key=session_secret(),
            session_cookie="online_dekanat_session",
            max_age=60 * 60 * 12,
            same_site="lax",
            https_only=os.getenv("ONLINE_DEKANAT_HTTPS_ONLY", "").lower() == "true",
        )
    ],
)
_database = StudentDatabase()
_collaboration = CollaborationStore()


class Credentials(BaseModel):
    email: str
    password: str


class RegistrationRequest(BaseModel):
    email: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class StaffInvitation(BaseModel):
    email: str


class ChatSockets:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, conversation_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(conversation_id, set()).add(websocket)

    def disconnect(self, conversation_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(conversation_id)
        if connections is not None:
            connections.discard(websocket)
            if not connections:
                self._connections.pop(conversation_id, None)

    async def broadcast(self, conversation_id: str, message: dict) -> None:
        for websocket in tuple(self._connections.get(conversation_id, ())):
            try:
                await websocket.send_json({"type": "message", "message": message})
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(conversation_id, websocket)


_chat_sockets = ChatSockets()


@app.middleware("http")
async def limit_chat_upload_body(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    if (
        request.method == "POST"
        and request.url.path.startswith("/chat/conversations/")
        and request.url.path.endswith("/messages")
    ):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_CHAT_FILE_SIZE + 1024 * 1024:
            return JSONResponse(
                {"detail": "Размер файла не должен превышать 20 МБ"},
                status_code=413,
            )
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/me")
def auth_me(request: Request) -> dict:
    user = _session_user(request)
    return {"user": user}


@app.post("/auth/register/request", status_code=201)
def register_staff(payload: RegistrationRequest, request: Request) -> dict[str, str]:
    _check_same_origin(request)
    try:
        email = _collaboration.validate_email(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _collaboration.create_staff_with_temporary_password(
            email,
            lambda password: send_temporary_password(email, password),
        )
    except ValueError as exc:
        status_code = 409 if "уже существует" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "message": (
            "Аккаунт создан. Временный пароль отправлен на вашу почту. "
            "При желании его можно изменить в профиле."
        )
    }


@app.post("/auth/login")
def login(credentials: Credentials, request: Request) -> dict:
    _check_same_origin(request)
    try:
        email = _collaboration.validate_email(credentials.email)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Неверный адрес или пароль") from exc
    user = _collaboration.authenticate(email, credentials.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Неверный адрес или пароль")
    request.session.clear()
    request.session["user_id"] = user["id"]
    return {"user": user}


@app.post("/auth/logout")
def logout(request: Request) -> dict[str, str]:
    _check_same_origin(request)
    request.session.clear()
    return {"status": "ok"}


@app.get("/auth/users")
def list_staff(request: Request) -> list[dict]:
    _require_admin(request)
    return _collaboration.list_users()


@app.post("/auth/users")
def create_staff(payload: StaffInvitation, request: Request) -> dict:
    _check_same_origin(request)
    _require_admin(request)
    try:
        email = _collaboration.validate_email(payload.email)
        return _collaboration.create_staff_with_temporary_password(
            email,
            lambda password: send_temporary_password(email, password),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/auth/change-password")
def change_password(payload: PasswordChange, request: Request) -> dict:
    _check_same_origin(request)
    user = _session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход сотрудника")
    try:
        updated = _collaboration.change_password(
            user["id"], payload.current_password, payload.new_password
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    return {"user": _session_user(request)}


@app.post("/process")
async def process_document(
    file: UploadFile = File(...),
    question: str = Form(...),
    doc_type: str | None = Form(default=None),
    ocr: str = Form(default="auto"),
):
    suffix = Path(file.filename or "document").suffix or ".bin"
    storage = EncryptedStorage()
    tmp_path = None
    try:
        tmp_path = storage.save(await file.read(), suffix=suffix)
        result = DeanOfficePipeline(ocr_name=ocr).process(tmp_path, question=question, doc_type=doc_type or None)
        return result.to_dict()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.get("/highlights/{filename}")
def highlight(filename: str) -> FileResponse:
    root = Path("data/highlights").resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        return JSONResponse({"error": "highlight not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.get("/students")
def students(request: Request) -> list[dict]:
    _require_staff(request)
    return _database.list_students()


@app.get("/students/{student_id}/documents")
def student_documents(student_id: int, request: Request) -> list[dict]:
    _require_staff(request)
    return _database.list_documents(student_id)


@app.get("/chat/current")
def current_conversation(request: Request) -> dict:
    conversation_id = request.session.get("visitor_conversation_id")
    visitor_token = request.session.get("visitor_token")
    if not conversation_id or not visitor_token:
        return {"conversation": None}
    if not _collaboration.visitor_owns(conversation_id, visitor_token):
        request.session.pop("visitor_conversation_id", None)
        request.session.pop("visitor_token", None)
        return {"conversation": None}
    return {"conversation": {"id": conversation_id}}


@app.post("/chat/conversations")
def create_conversation(request: Request) -> dict:
    _check_same_origin(request)
    if _session_user(request) is not None:
        raise HTTPException(status_code=409, detail="Сотрудники используют список обращений")
    visitor_token = request.session.get("visitor_token")
    conversation_id = request.session.get("visitor_conversation_id")
    if visitor_token and conversation_id and _collaboration.visitor_owns(conversation_id, visitor_token):
        return {"conversation": {"id": conversation_id}}
    visitor_token = os.urandom(32).hex()
    conversation = _collaboration.create_conversation(visitor_token)
    request.session["visitor_token"] = visitor_token
    request.session["visitor_conversation_id"] = conversation["id"]
    return {"conversation": conversation}


@app.get("/chat/conversations")
def list_conversations(request: Request) -> list[dict]:
    _require_staff(request)
    return _collaboration.list_conversations()


@app.get("/chat/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, request: Request, after: int = 0) -> list[dict]:
    _authorize_conversation(conversation_id, request)
    if after < 0:
        raise HTTPException(status_code=400, detail="Некорректный номер сообщения")
    return _collaboration.list_messages(conversation_id, after)


@app.post("/chat/conversations/{conversation_id}/messages")
async def create_message(
    conversation_id: str,
    request: Request,
    body: str = Form(default=""),
    file: UploadFile | None = File(default=None),
) -> dict:
    _check_same_origin(request)
    user = _authorize_conversation(conversation_id, request)
    if len(body) > 20_000:
        raise HTTPException(status_code=400, detail="Сообщение не должно превышать 20 000 символов")
    stored_file = None
    encrypted_path: Path | None = None
    try:
        if file is not None and file.filename:
            original_name = file.filename.replace("\\", "/").split("/")[-1]
            original_name = "".join(char for char in original_name if char.isprintable()).strip()
            if not original_name:
                raise HTTPException(status_code=400, detail="Некорректное имя файла")
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_CHAT_EXTENSIONS:
                raise HTTPException(status_code=400, detail="Формат файла не поддерживается")
            payload = await file.read(MAX_CHAT_FILE_SIZE + 1)
            if len(payload) > MAX_CHAT_FILE_SIZE:
                raise HTTPException(status_code=413, detail="Размер файла не должен превышать 20 МБ")
            if not payload:
                raise HTTPException(status_code=400, detail="Нельзя отправить пустой файл")
            storage_root = os.getenv("ONLINE_DEKANAT_STORAGE_ROOT", "data/secure")
            encrypted_path = EncryptedStorage(storage_root).save(payload, suffix=".bin")
            file_id = os.urandom(16).hex()
            stored_file = {
                "id": file_id,
                "storage_name": encrypted_path.name,
                "original_name": original_name[:180],
                "media_type": "application/octet-stream",
                "size": len(payload),
            }
        message = _collaboration.add_message(
            conversation_id,
            "staff" if user else "visitor",
            body,
            sender_id=user["id"] if user else None,
            file=stored_file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if encrypted_path is not None:
            encrypted_path.unlink(missing_ok=True)
        raise
    finally:
        if file is not None:
            await file.close()
    await _chat_sockets.broadcast(conversation_id, message)
    return message


@app.get("/chat/files/{file_id}")
def download_chat_file(file_id: str, request: Request) -> Response:
    file_record = _collaboration.get_file(file_id)
    if file_record is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    _authorize_conversation(file_record["conversation_id"], request)
    storage_root = Path(os.getenv("ONLINE_DEKANAT_STORAGE_ROOT", "data/secure")).resolve()
    encrypted_path = (storage_root / file_record["storage_name"]).resolve()
    if encrypted_path.parent != storage_root or not encrypted_path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    storage = EncryptedStorage(storage_root)
    with storage.decrypted_temporary_file(encrypted_path) as temporary:
        payload = temporary.read_bytes()
    filename = quote(file_record["original_name"], safe="")
    return Response(
        payload,
        media_type=file_record["media_type"],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@app.websocket("/chat/ws/{conversation_id}")
async def chat_websocket(websocket: WebSocket, conversation_id: str) -> None:
    if not _origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host", "")):
        await websocket.close(code=1008)
        return
    session = websocket.scope.get("session", {})
    user_id = session.get("user_id")
    user = _collaboration.get_user(user_id) if isinstance(user_id, int) else None
    visitor_token = session.get("visitor_token")
    if not (
        (user and _collaboration.conversation_exists(conversation_id))
        or (visitor_token and _collaboration.visitor_owns(conversation_id, visitor_token))
    ):
        await websocket.close(code=1008)
        return
    await _chat_sockets.connect(conversation_id, websocket)
    try:
        while True:
            if await websocket.receive_text() == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _chat_sockets.disconnect(conversation_id, websocket)


@app.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    return UI_HTML


def _session_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    return _collaboration.get_user(user_id) if isinstance(user_id, int) else None


def _require_staff(request: Request) -> dict:
    user = _session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход сотрудника")
    return user


def _require_admin(request: Request) -> dict:
    user = _require_staff(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user


def _authorize_conversation(conversation_id: str, request: Request) -> dict | None:
    user = _session_user(request)
    if user and _collaboration.conversation_exists(conversation_id):
        return user
    visitor_token = request.session.get("visitor_token")
    if visitor_token and _collaboration.visitor_owns(conversation_id, visitor_token):
        return None
    raise HTTPException(status_code=404, detail="Обращение не найдено")


def _check_same_origin(request: Request) -> None:
    if not _origin_allowed(request.headers.get("origin"), request.headers.get("host", "")):
        raise HTTPException(status_code=403, detail="Запрос с другого сайта запрещен")


def _origin_allowed(origin: str | None, host: str) -> bool:
    if origin is None:
        return True
    try:
        return urlsplit(origin).netloc.lower() == host.lower()
    except ValueError:
        return False
