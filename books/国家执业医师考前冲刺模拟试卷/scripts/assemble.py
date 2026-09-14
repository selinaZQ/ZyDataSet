"""Remove layout noise, restore reading order, and build Markdown/JSONL outputs."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from common import PROJECT_DIR, bbox_union, load_config, normalize_text, raw_page_path


HEADER_TOKENS = ("考前冲刺模拟试卷", "参考答案及解析")
FULL_PATTERNS = (
    re.compile(r"国家执业医师资格考试"),
    re.compile(r"考前冲刺模拟试卷"),
    re.compile(r"参考答案及解析"),
    re.compile(r"第[一二三四]单元"),
    re.compile(r"[ABX][1-4]?\s*型.*题", re.I),
    re.compile(r"答题说明"),
)
QUESTION_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")
OPTION_RE = re.compile(r"^([A-E])\.\s*(.*)$")
ANSWER_RE = re.compile(r"^(\d{1,3})\.\s*([A-E])\s*[【\[]解析[】\]]\s*(.*)$")


def is_noise(line: dict[str, Any], width: float, height: float) -> bool:
    text = line["text"].strip()
    y_mid = (line["bbox"][1] + line["bbox"][3]) / 2
    if y_mid < height * 0.105 and any(token in text for token in HEADER_TOKENS):
        return True
    if y_mid > height * 0.955 and re.fullmatch(r"[—\-–_ ]*\d+[—\-–_ ]*", text):
        return True
    if re.fullmatch(r"[—\-–_ ]+", text):
        return True
    if not text:
        return True
    return False


def is_full(line: dict[str, Any], width: float) -> bool:
    text = line["text"]
    if line.get("column_hint") == "full":
        return True
    box = line["bbox"]
    centered = box[0] < width * 0.48 and box[2] > width * 0.52
    return centered and any(pattern.search(text) for pattern in FULL_PATTERNS)


def join_fragments(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    separator = ""
    if left[-1:].isascii() and right[:1].isascii() and left[-1:].isalnum() and right[:1].isalnum():
        separator = " "
    return normalize_text(left + separator + right)


def merge_same_row_fragments(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for line in sorted(lines, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2, item["bbox"][0])):
        center = (line["bbox"][1] + line["bbox"][3]) / 2
        height = max(1.0, line["bbox"][3] - line["bbox"][1])
        for row in reversed(rows[-3:]):
            row_center = sum((item["bbox"][1] + item["bbox"][3]) / 2 for item in row) / len(row)
            row_height = min(max(1.0, item["bbox"][3] - item["bbox"][1]) for item in row)
            if abs(center - row_center) <= min(height, row_height) * 0.42:
                row.append(line)
                break
        else:
            rows.append([line])

    merged: list[dict[str, Any]] = []
    for row in rows:
        row = sorted(row, key=lambda item: item["bbox"][0])
        current = dict(row[0])
        current["source_line_ids"] = [row[0]["line_id"]]
        for fragment in row[1:]:
            current["text"] = join_fragments(current["text"], fragment["text"])
            current["bbox"] = bbox_union([current["bbox"], fragment["bbox"]])
            current["confidence"] = min(float(current["confidence"]), float(fragment["confidence"]))
            current["source_line_ids"].append(fragment["line_id"])
        merged.append(current)
    return merged


def reading_order(lines: list[dict[str, Any]], width: float, height: float) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for line in lines:
        line = dict(line)
        line["text"] = normalize_text(line["text"])
        if is_noise(line, width, height):
            continue
        if is_full(line, width):
            line["column"] = "full"
        elif (line["bbox"][0] + line["bbox"][2]) / 2 < width / 2:
            line["column"] = "left"
        else:
            line["column"] = "right"
        cleaned.append(line)

    regrouped: list[dict[str, Any]] = []
    for column in ("full", "left", "right"):
        regrouped.extend(merge_same_row_fragments([line for line in cleaned if line["column"] == column]))
    cleaned = regrouped

    full = sorted((line for line in cleaned if line["column"] == "full"), key=lambda x: x["bbox"][1])
    ordered: list[dict[str, Any]] = []
    lower = float("-inf")
    for marker in full:
        upper = marker["bbox"][1]
        band = [line for line in cleaned if line["column"] != "full" and lower <= line["bbox"][1] < upper]
        ordered.extend(sorted((x for x in band if x["column"] == "left"), key=lambda x: (x["bbox"][1], x["bbox"][0])))
        ordered.extend(sorted((x for x in band if x["column"] == "right"), key=lambda x: (x["bbox"][1], x["bbox"][0])))
        ordered.append(marker)
        lower = marker["bbox"][3]
    band = [line for line in cleaned if line["column"] != "full" and line["bbox"][1] >= lower]
    ordered.extend(sorted((x for x in band if x["column"] == "left"), key=lambda x: (x["bbox"][1], x["bbox"][0])))
    ordered.extend(sorted((x for x in band if x["column"] == "right"), key=lambda x: (x["bbox"][1], x["bbox"][0])))
    for index, line in enumerate(ordered, start=1):
        line["reading_order"] = index
    return ordered


def heading_level(text: str) -> int | None:
    if re.fullmatch(r"第[一二三四]单元", text):
        return 2
    if re.search(r"[ABX][1-4]?\s*型.*题", text, re.I):
        return 3
    if "答题说明" in text:
        return 4
    if "国家执业医师资格考试" in text or "考前冲刺模拟试卷" in text or text == "参考答案及解析":
        return 1
    return None


def render_markdown(title: str, pages: list[dict[str, Any]]) -> str:
    output = [f"# {title}", ""]
    seen_headings: set[str] = {title}
    for page in pages:
        output.extend([f"<!-- PDF page {page['page']} -->", ""])
        for line in page["lines"]:
            text = line["text"]
            level = heading_level(text)
            if level:
                if text in seen_headings and level == 1:
                    continue
                seen_headings.add(text)
                output.extend(["#" * level + " " + text, ""])
            elif OPTION_RE.match(text):
                output.extend(["   " + text + "  ", ""])
            elif QUESTION_RE.match(text) or ANSWER_RE.match(text):
                output.extend([text, ""])
            else:
                output.extend([text, ""])
    return "\n".join(output).rstrip() + "\n"


def main() -> None:
    config = load_config()
    merged_pages_path = PROJECT_DIR / "03_structured" / "pages.jsonl"
    merged_pages_path.parent.mkdir(parents=True, exist_ok=True)
    all_documents: list[dict[str, Any]] = []
    with merged_pages_path.open("w", encoding="utf-8", newline="\n") as merged_stream:
        for document in config["documents"]:
            first = raw_page_path(document["slug"], 1)
            if not first.exists():
                raise FileNotFoundError(f"Missing extraction: {first}")
            first_data = json.loads(first.read_text(encoding="utf-8"))
            pages: list[dict[str, Any]] = []
            line_counts: Counter[str] = Counter()
            for page_number in range(1, int(first_data["page_count"]) + 1):
                data = json.loads(raw_page_path(document["slug"], page_number).read_text(encoding="utf-8"))
                lines = reading_order(data["lines"], float(data["width"]), float(data["height"]))
                for line in lines:
                    line["page"] = page_number
                    line_counts[line["column"]] += 1
                page_record = {
                    "document": document["slug"],
                    "source_file": document["file"],
                    "source_sha256": data["source_sha256"],
                    "page": page_number,
                    "extraction_mode": data["extraction_mode"],
                    "lines": lines,
                }
                pages.append(page_record)
                merged_stream.write(json.dumps(page_record, ensure_ascii=False) + "\n")

            clean_dir = PROJECT_DIR / "02_clean"
            clean_dir.mkdir(parents=True, exist_ok=True)
            markdown = render_markdown(document["title"], pages)
            (clean_dir / f"{document['slug']}.ordered.md").write_text(markdown, encoding="utf-8", newline="\n")
            with (clean_dir / f"{document['slug']}.lines.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
                for page in pages:
                    for line in page["lines"]:
                        stream.write(json.dumps(line, ensure_ascii=False) + "\n")
            all_documents.append({**document, "page_count": len(pages), "line_counts": dict(line_counts)})
            print(f"[assemble] {document['slug']}: {len(pages)} pages", flush=True)

    (PROJECT_DIR / "03_structured" / "manifest.json").write_text(
        json.dumps({"documents": all_documents}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
