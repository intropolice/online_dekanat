from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def load_mws_vision_bench(config: str = "default", split: str = "train"):
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Для работы с набором данных установите дополнительный набор зависимостей dataset.") from exc
    return load_dataset("MTSAIR/MWS-Vision-Bench", config, split=split)


def iter_mws_samples(config: str = "default", split: str = "train", task_type: str | None = None, limit: int | None = None) -> Iterable[dict]:
    dataset = load_mws_vision_bench(config=config, split=split)
    count = 0
    for item in dataset:
        if task_type and item.get("type") != task_type:
            continue
        yield item
        count += 1
        if limit is not None and count >= limit:
            break


def export_mws_sample(out_path: str | Path, config: str = "default", task_type: str | None = None, limit: int = 20) -> Path:
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for item in iter_mws_samples(config=config, task_type=task_type, limit=limit):
            record = {
                "id": item["id"],
                "type": item["type"],
                "dataset_name": item["dataset_name"],
                "question": item["question"],
                "answers": item["answers"],
            }
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return output
