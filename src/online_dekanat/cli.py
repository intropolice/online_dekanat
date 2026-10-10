from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from online_dekanat.dataset import export_mws_sample
from online_dekanat.pipeline import DeanOfficePipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="online-dekanat")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Run the OCR -> fields -> answer pipeline.")
    process.add_argument("--file", required=True, help="Path to JPG, PNG, PDF, or TXT in text OCR mode.")
    process.add_argument("--question", required=True, help="Student question in Russian.")
    process.add_argument("--doc-type", default=None, help="Optional document type id.")
    process.add_argument("--ocr", default="auto", choices=["auto", "qwen", "tesseract", "text"], help="OCR engine; auto selects the available local backend.")
    process.add_argument("--out", default=None, help="Optional JSON output path.")

    serve = subparsers.add_parser("serve", help="Start FastAPI server.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--backend", default="auto", choices=["auto", "fastapi", "simple"])

    sample = subparsers.add_parser("mws-sample", help="Export a local sample from MTSAIR/MWS-Vision-Bench.")
    sample.add_argument("--out", default="data/mws_sample.jsonl")
    sample.add_argument("--config", default="default")
    sample.add_argument("--task-type", default=None)
    sample.add_argument("--limit", default=20, type=int)

    args = parser.parse_args()
    if args.command == "process":
        result = DeanOfficePipeline(ocr_name=args.ocr).process(args.file, args.question, doc_type=args.doc_type)
        payload = result.to_dict()
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            output = Path(args.out)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text + "\n", encoding="utf-8")
        print(text)
    elif args.command == "serve":
        backend = _select_server_backend(args.backend)
        if backend == "simple":
            from online_dekanat.simple_server import run_simple_server

            run_simple_server(args.host, args.port)
        else:
            import uvicorn

            loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else "auto"
            uvicorn.run("online_dekanat.api:app", host=args.host, port=args.port, reload=False, loop=loop_factory)
    elif args.command == "mws-sample":
        output = export_mws_sample(args.out, config=args.config, task_type=args.task_type, limit=args.limit)
        print(f"Wrote {output}")


def _select_server_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if sys.platform == "win32":
        import socket

        if getattr(socket.socketpair, "__name__", "") == "_fallback_socketpair":
            return "simple"
    return "fastapi"


if __name__ == "__main__":
    main()
