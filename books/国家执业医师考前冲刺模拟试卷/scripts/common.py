"""Shared helpers for the exam-book extraction pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parents[1]
CONFIG_PATH = PROJECT_DIR / "config" / "documents.json"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def source_path(document: dict[str, Any]) -> Path:
    config = load_config()
    return REPO_DIR / config["source_dir"] / document["file"]


def raw_page_path(slug: str, page: int) -> Path:
    return PROJECT_DIR / "01_raw" / slug / "json" / f"page-{page:03d}.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bbox_union(boxes: Iterable[list[float]]) -> list[float]:
    boxes = list(boxes)
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def normalize_text(text: str) -> str:
    replacements = {
        "•": "·",
        "．": ".",
        "﹒": ".",
        "~": "～",
        "(": "（",
        ")": "）",
        "［": "[",
        "］": "]",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"^(\d{1,3})\s*[.、]\s*", r"\1. ", text)
    text = re.sub(r"^([A-E])\s*[.、]\s*", r"\1. ", text)
    text = re.sub(r"^(第[一二三四五六七八九十]+单元)\s*", r"\1", text)
    text = re.sub(r"A\s*1\s*型", "A1 型", text, flags=re.I)
    text = re.sub(r"A\s*2\s*型", "A2 型", text, flags=re.I)
    text = re.sub(r"A\s*3\s*型", "A3 型", text, flags=re.I)
    text = re.sub(r"A[lI]\s*型", "A1 型", text, flags=re.I)
    text = re.sub(r"B\s*1\s*型", "B1 型", text, flags=re.I)
    text = re.sub(r"B\s*2\s*型", "B2 型", text, flags=re.I)
    text = re.sub(r"X\s*型", "X 型", text, flags=re.I)
    text = re.sub(r"（\s+", "（", text)
    text = re.sub(r"\s+）", "）", text)
    return text


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_raw_markdown(slug: str, page: int, lines: list[dict[str, Any]]) -> None:
    path = PROJECT_DIR / "01_raw" / slug / "markdown" / f"page-{page:03d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"<!-- PDF page {page}; raw geometric order -->", ""]
    for line in sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        body.extend([line["text"], ""])
    path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8", newline="\n")
