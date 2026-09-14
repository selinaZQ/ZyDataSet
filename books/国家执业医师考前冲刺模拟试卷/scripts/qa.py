"""Structural and provenance QA for the full extracted collection."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import PROJECT_DIR, load_config, sha256, source_path


EXPECTED_UNITS = ("一", "二", "三", "四")
EXPECTED_NUMBERS = set(range(1, 151))
EXPECTED_OPTIONS = {"A", "B", "C", "D", "E"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def validate_number_groups(
    rows: list[dict[str, Any]],
    group_field: str,
    errors: list[dict[str, Any]],
    source: str,
) -> None:
    grouped: defaultdict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_field])].append(int(row["question_no"]))
    for group in EXPECTED_UNITS:
        numbers = grouped[group]
        missing = sorted(EXPECTED_NUMBERS - set(numbers))
        duplicates = sorted(number for number, count in Counter(numbers).items() if count > 1)
        if len(numbers) != 150 or missing or duplicates:
            errors.append({
                "source": source,
                "group": group,
                "reason": "question_number_sequence",
                "count": len(numbers),
                "missing": missing,
                "duplicates": duplicates,
            })


def main() -> None:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    document_reports: list[dict[str, Any]] = []
    question_keys: set[tuple[str, str, int]] = set()
    answer_keys: set[tuple[str, str, int]] = set()
    exam_label_by_slug = {"exam-01": "一", "exam-02": "二", "exam-03": "三", "exam-04": "四"}

    for document in load_config()["documents"]:
        source = source_path(document)
        page_files = sorted((PROJECT_DIR / "01_raw" / document["slug"] / "json").glob("page-*.json"))
        if not page_files:
            errors.append({"document": document["slug"], "reason": "no_raw_pages"})
            continue
        first = json.loads(page_files[0].read_text(encoding="utf-8"))
        expected_pages = int(first["page_count"])
        actual_pages = len(page_files)
        if actual_pages != expected_pages:
            errors.append({"document": document["slug"], "reason": "page_count", "expected": expected_pages, "actual": actual_pages})
        if first["source_sha256"] != sha256(source):
            errors.append({"document": document["slug"], "reason": "source_hash_mismatch"})

        clean_path = PROJECT_DIR / "02_clean" / f"{document['slug']}.md"
        ordered_path = PROJECT_DIR / "02_clean" / f"{document['slug']}.ordered.md"
        text = clean_path.read_text(encoding="utf-8") if clean_path.exists() else ""
        ordered_text = ordered_path.read_text(encoding="utf-8") if ordered_path.exists() else ""
        page_markers = len(re.findall(r"<!-- PDF page \d+ -->", ordered_text))
        if page_markers != expected_pages:
            errors.append({"document": document["slug"], "reason": "page_markers", "expected": expected_pages, "actual": page_markers})
        residue = [token for token in ("_res.json",) if token in text]
        if residue:
            warnings.append({"document": document["slug"], "reason": "possible_noise_residue", "tokens": residue})

        low_confidence = 0
        line_count = 0
        for page_file in page_files:
            data = json.loads(page_file.read_text(encoding="utf-8"))
            for line in data["lines"]:
                line_count += 1
                if float(line["confidence"]) < 0.80:
                    low_confidence += 1
        if low_confidence:
            warnings.append({"document": document["slug"], "reason": "low_confidence_raw_lines", "count": low_confidence})

        suffix = "questions" if document["kind"] == "questions" else "answers"
        structured_path = PROJECT_DIR / "03_structured" / f"{document['slug']}.{suffix}.jsonl"
        rows = load_jsonl(structured_path) if structured_path.exists() else []
        if not rows:
            errors.append({"document": document["slug"], "reason": "missing_structured_records"})
        if document["kind"] == "questions":
            if len(rows) != 600:
                errors.append({"document": document["slug"], "reason": "question_count", "expected": 600, "actual": len(rows)})
            validate_number_groups(rows, "unit", errors, document["slug"])
            exam_label = exam_label_by_slug[document["slug"]]
            for row in rows:
                key = (exam_label, row["unit"], int(row["question_no"]))
                question_keys.add(key)
                if set(row["options"]) != EXPECTED_OPTIONS:
                    errors.append({"document": document["slug"], "reason": "option_set", "id": row["id"], "actual": list(row["options"])})
                if row.get("needs_review"):
                    review_rows.append({"record_type": "question", **row})
        else:
            if len(rows) != 2400:
                errors.append({"document": document["slug"], "reason": "answer_count", "expected": 2400, "actual": len(rows)})
            by_exam: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_exam[row["exam"]].append(row)
                key = (row["exam"], row["unit"], int(row["question_no"]))
                answer_keys.add(key)
                if row["answer"] not in EXPECTED_OPTIONS:
                    errors.append({"document": document["slug"], "reason": "invalid_answer", "id": row["id"], "answer": row["answer"]})
            for exam in ("一", "二", "三", "四"):
                validate_number_groups(by_exam[exam], "unit", errors, f"answers-exam-{exam}")

        document_reports.append({
            "document": document["slug"],
            "source_file": document["file"],
            "source_sha256": first["source_sha256"],
            "extraction_mode": document["extraction_mode"],
            "page_count": expected_pages,
            "raw_line_count": line_count,
            "low_confidence_raw_line_count": low_confidence,
            "structured_record_count": len(rows),
            "structured_review_count": sum(1 for row in rows if row.get("needs_review")),
            "records_without_source_analysis": sum(1 for row in rows if row.get("analysis_available") is False),
            "clean_markdown": str(clean_path.relative_to(PROJECT_DIR)).replace("\\", "/"),
            "ordered_markdown": str(ordered_path.relative_to(PROJECT_DIR)).replace("\\", "/"),
            "structured_jsonl": str(structured_path.relative_to(PROJECT_DIR)).replace("\\", "/"),
        })

    missing_answers = sorted(question_keys - answer_keys)
    orphan_answers = sorted(answer_keys - question_keys)
    if missing_answers:
        errors.append({"reason": "missing_answer_links", "count": len(missing_answers), "keys": missing_answers})
    if orphan_answers:
        errors.append({"reason": "orphan_answer_links", "count": len(orphan_answers), "keys": orphan_answers})

    merged = PROJECT_DIR / "04_merged" / "全集.md"
    if not merged.exists() or merged.stat().st_size == 0:
        errors.append({"reason": "missing_merged_markdown"})
    elif merged.read_text(encoding="utf-8").count("<!-- BEGIN ") != 5:
        errors.append({"reason": "merged_document_sections"})

    status = "fail" if errors else ("pass_with_review" if warnings or review_rows else "pass")
    report = {
        "status": status,
        "document_count": len(document_reports),
        "total_pages": sum(item["page_count"] for item in document_reports),
        "total_questions": len(question_keys),
        "total_answers": len(answer_keys),
        "linked_question_answer_pairs": len(question_keys & answer_keys),
        "review_record_count": len(review_rows),
        "documents": document_reports,
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    review_dir = PROJECT_DIR / "05_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "qa_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with (review_dir / "review.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for row in review_rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
