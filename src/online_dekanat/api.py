from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from online_dekanat.pipeline import DeanOfficePipeline
from online_dekanat.ui import UI_HTML
from online_dekanat.secure_storage import EncryptedStorage

app = FastAPI(title="Онлайн-деканат", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    return UI_HTML
