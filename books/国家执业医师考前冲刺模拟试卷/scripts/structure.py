"""Turn the ordered line stream into question and answer records."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any

from common import PROJECT_DIR, load_config, normalize_text


UNIT_RE = re.compile(r"^第([一二三四])单元$")
TYPE_RE = re.compile(r"^([ABX][1-4]?)\s*型.*题.*?[（(](\d{1,3})\s*[～~—-]\s*(\d{1,3})", re.I)
TYPE_RANGE_ONLY_RE = re.compile(r"^型.*题.*?[（(](\d{1,3})\s*[～~—-]\s*(\d{1,3})")
QUESTION_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")
QUESTION_ANY_RE = re.compile(r"(?<!\d)(\d{1,3})\.\s*(.*)$")
OPTION_RE = re.compile(r"^([A-E])\.\s*(.*)$")
OPTION_MARKER_RE = re.compile(r"(?<![A-Za-z])([A-E])[\.，,]\s*")
SHARED_RE = re.compile(r"[（(](\d{1,3})\s*[～~—-]\s*(\d{1,3})题共用备选答案[）)]")
ANSWER_RE = re.compile(r"^(\d{1,3})\.\s*([A-E])(?:\s*[【\[]解析[】\]])?\s*(.*)$")
CORRECTED_ANSWER_RE = re.compile(r"^(\d{1,3})[\.，,]\s*(.*)$")


def join_text(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    separator = " " if left[-1:].isascii() and right[:1].isascii() and left[-1:].isalnum() and right[:1].isalnum() else ""
    return normalize_text(left + separator + right)


def balance_parentheses(text: str) -> str:
    if text.count("（") == text.count("）") + 1 and text.rstrip().endswith("（"):
        return text.rstrip() + "）"
    return text


def split_option_segments(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a stem/line containing one or more inline A-E option markers."""
    matches = list(OPTION_MARKER_RE.finditer(text))
    if not matches:
        return text, []
    prefix = text[:matches[0].start()].strip()
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segments.append((match.group(1), text[match.end():end].strip()))
    return prefix, segments


def load_lines(slug: str) -> list[dict[str, Any]]:
    path = PROJECT_DIR / "02_clean" / f"{slug}.lines.jsonl"
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def source_ref(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "page": line["page"],
        "line_ids": line.get("source_line_ids", [line["line_id"]]),
        "confidence": line["confidence"],
        "bbox": line["bbox"],
    }


def parse_questions(document: dict[str, Any], lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unit = ""
    question_type = ""
    question_range: tuple[int, int] | None = None
    current: dict[str, Any] | None = None
    last_option: str | None = None
    shared_range: tuple[int, int] | None = None
    shared_options: OrderedDict[str, str] = OrderedDict()
    shared_refs: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    corrections = json.loads((PROJECT_DIR / "config" / "confirmed_question_corrections.json").read_text(encoding="utf-8"))
    correction_map = {
        (item["exam"], item["unit"], int(item["question_no"])): item
        for item in corrections
    }

    def finalize() -> None:
        nonlocal current, last_option
        if current is None:
            return
        current["question"] = balance_parentheses(current["question"])
        current["source_pages"] = sorted({ref["page"] for ref in current["source_refs"]})
        current["cross_page"] = len(current["source_pages"]) > 1
        current["ocr_min_confidence"] = round(min(ref["confidence"] for ref in current["source_refs"]), 6)
        correction = correction_map.get((document["slug"], current["unit"], current["question_no"]))
        if correction:
            current["options"].update(correction["option_updates"])
            current["options"] = OrderedDict(sorted(current["options"].items()))
            current["confirmed_correction"] = {
                "source_page": int(correction["page"]),
                "option_updates": correction["option_updates"],
                "verification": correction["verification"],
            }
        reasons: list[str] = []
        if not current["unit"]:
            reasons.append("missing_unit")
        if not current["question_type"]:
            reasons.append("missing_question_type")
        if not current["question"]:
            reasons.append("empty_question")
        if set(current["options"]) != {"A", "B", "C", "D", "E"}:
            reasons.append("option_set:" + ",".join(current["options"].keys()))
        if current["ocr_min_confidence"] < 0.80:
            reasons.append("low_ocr_confidence")
        current["needs_review"] = bool(reasons)
        current["review_reason"] = reasons
        current["id"] = f"{document['slug']}-unit-{unit or 'unknown'}-{question_type or 'unknown'}-{current['question_no']:03d}"
        records.append(current)
        current = None
        last_option = None

    for line in lines:
        text = line["text"]
        unit_match = UNIT_RE.match(text)
        if unit_match:
            finalize()
            unit = unit_match.group(1)
            question_type = ""
            question_range = None
            shared_range = None
            shared_options = OrderedDict()
            shared_refs = []
            continue
        type_match = TYPE_RE.match(text)
        if type_match:
            finalize()
            question_type = type_match.group(1).upper()
            question_range = (int(type_match.group(2)), int(type_match.group(3)))
            shared_range = None
            shared_options = OrderedDict()
            shared_refs = []
            continue
        range_only_match = TYPE_RANGE_ONLY_RE.match(text)
        if range_only_match:
            finalize()
            question_range = (int(range_only_match.group(1)), int(range_only_match.group(2)))
            if question_range == (119, 120):
                question_type = "A3"
            continue
        shared_match = SHARED_RE.search(text)
        if shared_match:
            finalize()
            shared_range = (int(shared_match.group(1)), int(shared_match.group(2)))
            shared_options = OrderedDict()
            shared_refs = [source_ref(line)]
            continue
        option_prefix, option_segments = split_option_segments(text)
        if option_segments and (current is not None or shared_range is not None):
            if option_prefix and current is not None:
                if last_option is None:
                    current["question"] = join_text(current["question"], option_prefix)
                else:
                    current["options"][last_option] = join_text(current["options"][last_option], option_prefix)
            for label, value in option_segments:
                if current is None and shared_range is not None:
                    shared_options[label] = value
                elif current is not None:
                    current["options"][label] = value
                    last_option = label
            if current is None and shared_range is not None:
                shared_refs.append(source_ref(line))
            elif current is not None:
                current["source_refs"].append(source_ref(line))
            continue
        question_match = QUESTION_RE.match(text)
        if question_match is None and current is not None and set(current["options"]) == {"A", "B", "C", "D", "E"}:
            embedded_match = QUESTION_ANY_RE.search(text)
            if embedded_match:
                embedded_number = int(embedded_match.group(1))
                if question_range and question_range[0] <= embedded_number <= question_range[1]:
                    text = text[embedded_match.start():]
                    question_match = QUESTION_RE.match(text)
        if question_match:
            number = int(question_match.group(1))
            if number == 0 or (question_range and not question_range[0] <= number <= question_range[1]):
                if current is not None:
                    if last_option is None:
                        current["question"] = join_text(current["question"], text)
                    else:
                        current["options"][last_option] = join_text(current["options"][last_option], text)
                    current["source_refs"].append(source_ref(line))
                continue
            finalize()
            options: OrderedDict[str, str] = OrderedDict()
            refs = [source_ref(line)]
            applied_shared_range = None
            if shared_range and shared_range[0] <= number <= shared_range[1]:
                options.update(shared_options)
                refs = shared_refs + refs
                applied_shared_range = list(shared_range)
            question_text, inline_options = split_option_segments(question_match.group(2))
            for label, value in inline_options:
                options[label] = value
            current = {
                "source_file": document["file"],
                "exam": document["slug"],
                "unit": unit,
                "question_type": question_type,
                "question_no": number,
                "question": question_text,
                "options": options,
                "shared_options_range": applied_shared_range,
                "source_refs": refs,
            }
            last_option = inline_options[-1][0] if inline_options else None
            continue
        if current is not None:
            if last_option is None:
                current["question"] = join_text(current["question"], text)
            else:
                current["options"][last_option] = join_text(current["options"][last_option], text)
            current["source_refs"].append(source_ref(line))
    finalize()
    return records


def parse_answers(document: dict[str, Any], lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exam = ""
    unit = ""
    current: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []
    corrections = json.loads((PROJECT_DIR / "config" / "confirmed_answer_corrections.json").read_text(encoding="utf-8"))
    correction_map = {
        (item["exam"], item["unit"], int(item["question_no"])): item
        for item in corrections
    }

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        current["source_pages"] = sorted({ref["page"] for ref in current["source_refs"]})
        current["cross_page"] = len(current["source_pages"]) > 1
        current["ocr_min_confidence"] = round(min(ref["confidence"] for ref in current["source_refs"]), 6)
        current["analysis_available"] = bool(current["analysis"])
        current["needs_review"] = False
        current["review_reason"] = []
        current["id"] = f"answers-{exam or 'unknown'}-unit-{unit or 'unknown'}-{current['question_no']:03d}"
        records.append(current)
        current = None

    for line in lines:
        text = line["text"]
        exam_match = re.search(r"模拟试卷[（(]([一二三四])[）)]", text)
        if exam_match and ("国家执业医师" in text or line["column"] == "full"):
            finalize()
            exam = exam_match.group(1)
            unit = ""
            continue
        unit_match = UNIT_RE.match(text)
        if unit_match:
            finalize()
            unit = unit_match.group(1)
            continue
        corrected_match = CORRECTED_ANSWER_RE.match(text)
        if corrected_match:
            number = int(corrected_match.group(1))
            correction = correction_map.get((exam, unit, number))
            if correction and int(line["page"]) == int(correction["page"]):
                finalize()
                tail = corrected_match.group(2).strip()
                mode = correction["marker_mode"]
                if mode == "leading" and tail.startswith(correction["answer"]):
                    tail = tail[1:].lstrip()
                elif mode == "trailing" and tail.endswith(correction["answer"]):
                    tail = tail[:-1].rstrip()
                elif mode == "corrupt_a" and tail[:1] in {"λ", "入"}:
                    tail = tail[1:].lstrip()
                tail = re.sub(r"^[【\[]解析[】\]]\s*", "", tail)
                current = {
                    "source_file": document["file"],
                    "exam": exam,
                    "unit": unit,
                    "question_no": number,
                    "answer": correction["answer"],
                    "analysis": tail,
                    "source_refs": [source_ref(line)],
                    "confirmed_correction": {
                        "source_page": int(correction["page"]),
                        "marker_mode": mode,
                        "verification": "visually_checked_against_rendered_source",
                    },
                }
                continue
        answer_match = ANSWER_RE.match(text)
        if answer_match:
            finalize()
            current = {
                "source_file": document["file"],
                "exam": exam,
                "unit": unit,
                "question_no": int(answer_match.group(1)),
                "answer": answer_match.group(2),
                "analysis": answer_match.group(3),
                "source_refs": [source_ref(line)],
            }
            continue
        if current is not None:
            current["analysis"] = join_text(current["analysis"], text)
            current["source_refs"].append(source_ref(line))
    finalize()
    return records


def render_questions(document: dict[str, Any], records: list[dict[str, Any]]) -> str:
    output = [f"# {document['title']}", ""]
    current_context: tuple[str, str] | None = None
    rendered_shared: set[tuple[str, str, int, int]] = set()
    for record in records:
        context = (record["unit"], record["question_type"])
        if context != current_context:
            current_context = context
            output.extend([f"## 第{record['unit']}单元", "", f"### {record['question_type']} 型题", ""])
        shared = record.get("shared_options_range")
        if shared:
            key = (record["unit"], record["question_type"], shared[0], shared[1])
            if key not in rendered_shared:
                rendered_shared.add(key)
                output.extend([f"（{shared[0]}～{shared[1]}题共用备选答案）", ""])
                for label, value in record["options"].items():
                    output.append(f"   {label}. {value}  ")
                output.append("")
        output.extend([f"{record['question_no']}. {record['question']}", f"<!-- source_pages: {', '.join(map(str, record['source_pages']))} -->", ""])
        if not shared:
            for label, value in record["options"].items():
                output.append(f"   {label}. {value}  ")
            output.append("")
    return "\n".join(output).rstrip() + "\n"


def render_answers(document: dict[str, Any], records: list[dict[str, Any]]) -> str:
    output = [f"# {document['title']}", ""]
    current_context: tuple[str, str] | None = None
    for record in records:
        context = (record["exam"], record["unit"])
        if context != current_context:
            current_context = context
            output.extend([f"## 模拟试卷（{record['exam']}）", "", f"### 第{record['unit']}单元", ""])
        output.extend([
            f"{record['question_no']}. {record['answer']} 【解析】{record['analysis']}",
            f"<!-- source_pages: {', '.join(map(str, record['source_pages']))} -->",
            "",
        ])
    return "\n".join(output).rstrip() + "\n"


def main() -> None:
    output_dir = PROJECT_DIR / "03_structured"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()
    rendered_documents: list[tuple[dict[str, Any], str]] = []
    question_records: list[dict[str, Any]] = []
    answer_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    exam_label_by_slug = {"exam-01": "一", "exam-02": "二", "exam-03": "三", "exam-04": "四"}
    for document in config["documents"]:
        lines = load_lines(document["slug"])
        if document["kind"] == "questions":
            records = parse_questions(document, lines)
            markdown = render_questions(document, records)
            suffix = "questions"
            question_records.extend(records)
        else:
            records = parse_answers(document, lines)
            markdown = render_answers(document, records)
            suffix = "answers"
            answer_by_key = {
                (record["exam"], record["unit"], int(record["question_no"])): record
                for record in records
            }
        with (output_dir / f"{document['slug']}.{suffix}.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        (PROJECT_DIR / "02_clean" / f"{document['slug']}.md").write_text(markdown, encoding="utf-8", newline="\n")
        rendered_documents.append((document, markdown))
        print(f"[structure] {document['slug']}: {len(records)} records", flush=True)

    paired_path = output_dir / "questions_with_answers.jsonl"
    with paired_path.open("w", encoding="utf-8", newline="\n") as stream:
        for question in question_records:
            key = (
                exam_label_by_slug[question["exam"]],
                question["unit"],
                int(question["question_no"]),
            )
            answer = answer_by_key.get(key)
            if answer is None:
                raise ValueError(f"Missing answer for {key}")
            paired = {
                **question,
                "answer_record_id": answer["id"],
                "answer": answer["answer"],
                "analysis": answer["analysis"] if answer["analysis_available"] else "原书未提供解析。",
                "analysis_available": answer["analysis_available"],
                "answer_source_file": answer["source_file"],
                "answer_source_pages": answer["source_pages"],
                "answer_source_refs": answer["source_refs"],
            }
            if answer.get("confirmed_correction"):
                paired["answer_confirmed_correction"] = answer["confirmed_correction"]
            stream.write(json.dumps(paired, ensure_ascii=False) + "\n")
    print(f"[structure] paired questions with answers: {len(question_records)} records", flush=True)

    merged_dir = PROJECT_DIR / "04_merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    sections: list[str] = ["# 考前冲刺模拟试卷与参考答案及解析", ""]
    for document, markdown in rendered_documents:
        sections.extend([
            f"<!-- BEGIN {document['file']} -->",
            "",
            markdown.rstrip(),
            "",
            f"<!-- END {document['file']} -->",
            "",
        ])
    (merged_dir / "全集.md").write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
