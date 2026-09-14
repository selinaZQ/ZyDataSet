"""Render and OCR scan-only PDFs with PaddleOCR while preserving line boxes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from common import (
    PROJECT_DIR,
    REPO_DIR,
    load_config,
    normalize_text,
    raw_page_path,
    sha256,
    source_path,
    write_json,
    write_raw_markdown,
)


os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(REPO_DIR / ".cache" / "paddlex"))
os.environ.setdefault("PADDLE_HOME", str(REPO_DIR / ".cache" / "paddle"))
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import pypdfium2 as pdfium  # noqa: E402
from paddleocr import PaddleOCR  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", action="append", help="Process only selected slugs")
    parser.add_argument("--dpi", type=int, default=250)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def make_pipeline() -> PaddleOCR:
    return PaddleOCR(
        text_detection_model_name="PP-OCRv5_server_det",
        text_recognition_model_name="PP-OCRv5_server_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_limit_side_len=2400,
        text_det_limit_type="max",
        text_rec_score_thresh=0.0,
        enable_mkldnn=False,
        device="cpu",
    )


def main() -> None:
    args = parse_args()
    selected = set(args.slug or [])
    documents = [
        item
        for item in load_config()["documents"]
        if item["extraction_mode"] == "ocr" and (not selected or item["slug"] in selected)
    ]
    if not documents:
        return
    pipeline = make_pipeline()
    temp_dir = REPO_DIR / "tmp" / "pdfs" / "ocr-pages"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for document in documents:
        source = source_path(document)
        source_hash = sha256(source)
        pdf = pdfium.PdfDocument(source)
        for page_number in range(1, len(pdf) + 1):
            destination = raw_page_path(document["slug"], page_number)
            if destination.exists() and not args.force:
                continue
            image_path = temp_dir / f"{document['slug']}-page-{page_number:03d}.png"
            page = pdf[page_number - 1]
            image = page.render(scale=args.dpi / 72).to_pil()
            image.save(image_path, dpi=(args.dpi, args.dpi))
            result = list(pipeline.predict(str(image_path)))[0]
            lines: list[dict[str, Any]] = []
            for line_id, (text, score, box) in enumerate(
                zip(result["rec_texts"], result["rec_scores"], result["rec_boxes"])
            ):
                lines.append(
                    {
                        "line_id": line_id,
                        "text": normalize_text(str(text)),
                        "bbox": [float(value) for value in box],
                        "confidence": round(float(score), 6),
                        "column_hint": "unknown",
                        "source_mode": "ocr",
                    }
                )
            payload = {
                "source_file": document["file"],
                "source_sha256": source_hash,
                "page": page_number,
                "page_count": len(pdf),
                "width": image.width,
                "height": image.height,
                "render_dpi": args.dpi,
                "extraction_mode": "ocr",
                "ocr_models": {
                    "detection": "PP-OCRv5_server_det",
                    "recognition": "PP-OCRv5_server_rec"
                },
                "lines": lines,
            }
            write_json(destination, payload)
            write_raw_markdown(document["slug"], page_number, lines)
            image_path.unlink(missing_ok=True)
            print(f"[ocr] {document['slug']} page {page_number}/{len(pdf)}", flush=True)


if __name__ == "__main__":
    main()
