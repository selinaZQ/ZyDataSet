"""Extract positioned text from PDFs that contain a usable text layer."""

from __future__ import annotations

import argparse
from statistics import median
from typing import Any

import pdfplumber

from common import (
    bbox_union,
    load_config,
    normalize_text,
    raw_page_path,
    sha256,
    source_path,
    write_json,
    write_raw_markdown,
)


FULL_LINE_PATTERNS = (
    "国家执业医师资格考试",
    "考前冲刺模拟试卷",
    "参考答案及解析",
    "答题说明",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", action="append", help="Process only selected slugs")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def join_words(words: list[dict[str, Any]]) -> str:
    words = sorted(words, key=lambda word: word["x0"])
    parts: list[str] = []
    previous: dict[str, Any] | None = None
    for word in words:
        token = word["text"]
        if previous is not None:
            gap = float(word["x0"]) - float(previous["x1"])
            prev_text = previous["text"]
            ascii_boundary = prev_text[-1:].isascii() and token[:1].isascii()
            if ascii_boundary and gap > 1.5:
                parts.append(" ")
        parts.append(token)
        previous = word
    return normalize_text("".join(parts))


def group_rows(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        tolerance = max(2.2, float(word["height"]) * 0.28)
        for row in reversed(rows[-4:]):
            row_top = median(float(item["top"]) for item in row)
            if abs(float(word["top"]) - row_top) <= tolerance:
                row.append(word)
                break
        else:
            rows.append([word])
    return rows


def is_full_row(text: str, row: list[dict[str, Any]], width: float) -> bool:
    box = bbox_union(
        [[float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])] for w in row]
    )
    centered = box[0] < width * 0.48 and box[2] > width * 0.52
    re_module = __import__("re")
    heading = text.startswith("国家执业医师资格考试")
    heading = heading or text.startswith("考前冲刺模拟试卷")
    heading = heading or text == "参考答案及解析"
    heading = heading or text.startswith("答题说明")
    heading = heading or bool(re_module.fullmatch(r"第[一二三四]单元", text))
    heading = heading or bool(re_module.fullmatch(r"[ABX][1-4]?\s*型.*题[）)]?", text, re_module.I))
    return heading and centered


def make_line(words: list[dict[str, Any]], column: str, line_id: int) -> dict[str, Any]:
    boxes = [
        [float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"])]
        for word in words
    ]
    return {
        "line_id": line_id,
        "text": join_words(words),
        "bbox": [round(value, 3) for value in bbox_union(boxes)],
        "confidence": 1.0,
        "column_hint": column,
        "source_mode": "text_layer",
    }


def extract_page(page: pdfplumber.page.Page) -> list[dict[str, Any]]:
    words = page.extract_words(x_tolerance=2.2, y_tolerance=3.0, keep_blank_chars=False)
    divider = float(page.width) / 2
    output: list[dict[str, Any]] = []
    line_id = 0
    for row in group_rows(words):
        combined = join_words(row)
        if is_full_row(combined, row, float(page.width)):
            output.append(make_line(row, "full", line_id))
            line_id += 1
            continue
        for column, selected in (
            ("left", [w for w in row if (float(w["x0"]) + float(w["x1"])) / 2 < divider]),
            ("right", [w for w in row if (float(w["x0"]) + float(w["x1"])) / 2 >= divider]),
        ):
            if selected:
                output.append(make_line(selected, column, line_id))
                line_id += 1
    return [line for line in output if line["text"]]


def main() -> None:
    args = parse_args()
    selected = set(args.slug or [])
    for document in load_config()["documents"]:
        if document["extraction_mode"] != "text_layer":
            continue
        if selected and document["slug"] not in selected:
            continue
        source = source_path(document)
        source_hash = sha256(source)
        with pdfplumber.open(source) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                destination = raw_page_path(document["slug"], page_number)
                if destination.exists() and not args.force:
                    continue
                lines = extract_page(page)
                payload = {
                    "source_file": document["file"],
                    "source_sha256": source_hash,
                    "page": page_number,
                    "page_count": len(pdf.pages),
                    "width": float(page.width),
                    "height": float(page.height),
                    "extraction_mode": "text_layer",
                    "lines": lines,
                }
                write_json(destination, payload)
                write_raw_markdown(document["slug"], page_number, lines)
                print(f"[native] {document['slug']} page {page_number}/{len(pdf.pages)}", flush=True)


if __name__ == "__main__":
    main()
