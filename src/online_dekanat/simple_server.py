from __future__ import annotations

import json
import logging
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from online_dekanat.pipeline import DeanOfficePipeline
from online_dekanat.ui import STAFF_UI_HTML, UI_HTML
from online_dekanat.secure_storage import EncryptedStorage
from online_dekanat.database import StudentDatabase
from online_dekanat.uploads import MAX_UPLOAD_SIZE, validate_upload

logger = logging.getLogger(__name__)

class DeanOfficeHandler(BaseHTTPRequestHandler):
    server_version = "OnlineDekanatHTTP/0.1"

    def do_GET(self) -> None:
        if self.path in {"/", "/ui"}:
            self._send_text(UI_HTML, "text/html; charset=utf-8")
            return
        if self.path == "/staff":
            self._send_text(STAFF_UI_HTML, "text/html; charset=utf-8")
            return
        if self.path == "/health":
            self._send_json({"status": "ok", "server": "simple"})
            return
        if self.path.startswith("/highlights/"):
            root = Path("data/highlights").resolve()
            path = (root / self.path.removeprefix("/highlights/")).resolve()
            if path.parent == root and path.is_file():
                self._send_bytes(path.read_bytes(), "image/png")
                return
            self._send_json({"error": "Фрагмент документа не найден."}, status=HTTPStatus.NOT_FOUND)
            return
        if self.path == "/students":
            self._send_json(StudentDatabase().list_students())
            return
        if self.path.startswith("/students/") and self.path.endswith("/documents"):
            try:
                student_id = int(self.path.split("/")[2])
            except (IndexError, ValueError):
                self._send_json({"error": "invalid student id"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(StudentDatabase().list_documents(student_id))
            return
        self._send_json({"error": "Страница не найдена."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/process":
            self._send_json({"error": "Страница не найдена."}, status=HTTPStatus.NOT_FOUND)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_UPLOAD_SIZE + 1024 * 1024:
            self._send_json(
                {"error": "Размер файла не должен превышать 16 МБ."},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        try:
            fields, upload = self._read_request()
            if upload is None:
                raise ValueError("Необходимо выбрать файл.")
            question = fields.get("question", "")
            if not question:
                raise ValueError("Введите вопрос.")
            ocr = fields.get("ocr", "auto") or "auto"
            doc_type = fields.get("doc_type") or None
            result = DeanOfficePipeline(ocr_name=ocr).process(upload, question=question, doc_type=doc_type)
            self._send_json(result.to_dict())
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("Необходимо", "Введите", "Формат файла", "Размер файла", "Файл пустой")):
                message = str(exc)
            else:
                logger.exception("Не удалось обработать загруженный документ")
                message = "Не удалось обработать документ. Проверьте формат и качество файла и попробуйте ещё раз."
            self._send_json({"error": message}, status=HTTPStatus.BAD_REQUEST)
        finally:
            if "upload" in locals() and upload is not None:
                Path(upload).unlink(missing_ok=True)

    def _read_request(self) -> tuple[dict[str, str], str | None]:
        length = int(self.headers.get("Content-Length", "0"))
        content_type = self.headers.get("Content-Type", "")
        body = self.rfile.read(length)
        if content_type.startswith("multipart/form-data"):
            return self._read_multipart(content_type, body)
        fields = {key: values[-1] for key, values in parse_qs(body.decode("utf-8")).items()}
        return fields, None

    def _read_multipart(self, content_type: str, body: bytes) -> tuple[dict[str, str], str | None]:
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        fields: dict[str, str] = {}
        upload_path: str | None = None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if not name:
                continue
            if filename:
                try:
                    validate_upload(filename, len(payload))
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
                if not payload:
                    raise ValueError("Файл пустой.")
                suffix = Path(filename).suffix.lower()
                upload_path = str(EncryptedStorage().save(payload, suffix=suffix))
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")
        return fields, upload_path

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, content_type: str) -> None:
        self._send_bytes(text.encode("utf-8"), content_type)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_simple_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), DeanOfficeHandler)
    print(f"Serving simple HTTP on http://{host}:{port}/ui", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
