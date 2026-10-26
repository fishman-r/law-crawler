"""Local full-page OCR; OCR output always requires independent visual review.

No software or models are installed. Original PDF bytes are copied unchanged.
Bounding boxes retain engine reading order, not inferred table relationships.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run(args: list[str], timeout: int = 180) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr[-3000:]}")
    return result.stdout


def executable(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"Missing local executable {name}; install it separately or choose an available backend")
    return found


def probe(engine: str, languages: list[str]) -> dict:
    if engine == "apple-vision":
        if platform.system() != "Darwin":
            raise RuntimeError("apple-vision requires macOS; use installed tesseract on other platforms")
        helper = Path(__file__).with_name("apple_vision_ocr.swift")
        meta = json.loads(run([executable("swift"), str(helper), "--probe"]))
        meta["helper_sha256"] = sha256(helper)
    else:
        binary = executable("tesseract")
        meta = {"engine": engine, "version": run([binary, "--version"]).splitlines()[0],
                "supported_languages": run([binary, "--list-langs"]).splitlines()[1:]}
    missing = set(languages) - set(meta["supported_languages"])
    if missing:
        raise RuntimeError(f"Unsupported/uninstalled OCR languages: {sorted(missing)}; available: {meta['supported_languages']}")
    return meta


def recognize(image: Path, engine: str, languages: list[str]) -> dict:
    start = time.monotonic()
    if engine == "apple-vision":
        return json.loads(run([executable("swift"), str(Path(__file__).with_name("apple_vision_ocr.swift")), str(image), ",".join(languages)]))
    raw = run([executable("tesseract"), str(image), "stdout", "-l", "+".join(languages), "tsv"])
    rows = list(csv.DictReader(io.StringIO(raw), delimiter="\t"))
    observations = []
    for row in rows:
        if row.get("text", "").strip() and int(row["level"]) == 5:
            observations.append({"text": row["text"], "confidence": float(row["conf"]) / 100,
                                 "bbox": [int(row[k]) for k in ("left", "top", "width", "height")],
                                 "line_id": [int(row[k]) for k in ("block_num", "par_num", "line_num")]})
    return {"observations": observations, "raw_tsv": raw, "bbox_coordinate_system": "pixel_top_left_xywh",
            "duration_seconds": time.monotonic() - start, "languages": languages}


def observation_text(raw: dict) -> str:
    lines: list[str] = []
    previous = None
    for obs in raw["observations"]:
        line_id = obs.get("line_id")
        if line_id is not None and line_id == previous:
            lines[-1] += " " + obs["text"]
        else:
            lines.append(obs["text"])
        previous = line_id
    return "\n".join(lines)


def validate_cache(output: Path, pdf_sha: str, config_sha: str) -> dict:
    receipt = json.loads((output / "receipt.json").read_text())
    if receipt.get("status") != "completed" or receipt.get("pdf_sha256") != pdf_sha or receipt.get("config_sha256") != config_sha:
        raise RuntimeError("Output already exists with failed/incompatible inputs; choose a new output directory")
    for item in receipt["artifacts"]:
        path = output / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Cached artifact changed or missing: {item['path']}")
    return {**receipt, "reused": True}


def generate(pdf: Path, output: Path, engine: str, languages: list[str], dpi: int = 200) -> dict:
    start = time.monotonic()
    pdf = pdf.resolve()
    output = output.resolve()
    if dpi < 72 or dpi > 600:
        raise ValueError("dpi must be between 72 and 600")
    pdf_sha = sha256(pdf)
    config = {"version": 1, "engine": engine, "languages": languages, "dpi": dpi,
              "implementation_sha256": sha256(Path(__file__)), "helper_sha256": sha256(Path(__file__).with_name("apple_vision_ocr.swift")) if engine == "apple-vision" else None}
    config_sha = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if output.exists():
        return validate_cache(output, pdf_sha, config_sha)
    output.mkdir(parents=True)
    write_json(output / "config.json", config)
    try:
        if not pdf.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Input does not have a PDF signature")
        shutil.copyfile(pdf, output / "original.pdf")
        if sha256(output / "original.pdf") != pdf_sha:
            raise RuntimeError("Input changed during original preservation")
        engine_meta = probe(engine, languages)
        info = run([executable("pdfinfo"), str(output / "original.pdf")])
        match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.M)
        if not match:
            raise RuntimeError("Cannot establish actual PDF page count")
        page_count = int(match.group(1))
        renderer = executable("pdftoppm")
        version = subprocess.run([renderer, "-v"], capture_output=True, text=True, timeout=30)
        renderer_version = (version.stdout + version.stderr).splitlines()[0]
        pages = []
        for page in range(1, page_count + 1):
            prefix = output / f"page-{page:04d}"
            page_start = time.monotonic()
            run([renderer, "-f", str(page), "-l", str(page), "-singlefile", "-r", str(dpi), "-png", str(output / "original.pdf"), str(prefix)])
            image = prefix.with_suffix(".png")
            render_seconds = time.monotonic() - page_start
            raw = recognize(image, engine, languages)
            raw.update({"page": page, "pdf_sha256": pdf_sha, "image_sha256": sha256(image), "render_duration_seconds": render_seconds})
            raw_path = prefix.with_suffix(".ocr.json")
            write_json(raw_path, raw)
            text = observation_text(raw)
            if not text.strip():
                raise RuntimeError(f"Page {page} produced no OCR text; blank status cannot be inferred automatically")
            pages.append({"page": page, "text": text, "method": "ocr", "producer": json.dumps(engine_meta, ensure_ascii=False, sort_keys=True),
                          "evidence": f"{image}; raw observations: {raw_path}; unreviewed automatic OCR; table relations and non-text marks require independent review",
                          "image_path": str(image), "raw_ocr_path": str(raw_path), "ocr_duration_seconds": raw["duration_seconds"]})
        sidecar = {"version": 1, "pdf_sha256": pdf_sha, "page_count": page_count, "pages": pages,
                   "config_sha256": config_sha, "engine": engine_meta, "renderer": renderer_version,
                   "review_status": "needs_independent_visual_review", "table_structure_status": "not_inferred",
                   "duration_seconds": time.monotonic() - start}
        write_json(output / "transcription.json", sidecar)
        artifacts = [{"path": str(path.relative_to(output)), "sha256": sha256(path)} for path in sorted(output.iterdir()) if path.is_file()]
        receipt = {"status": "completed", "pdf_sha256": pdf_sha, "config_sha256": config_sha, "engine": engine_meta,
                   "page_count": page_count, "transcription_path": str(output / "transcription.json"), "artifacts": artifacts,
                   "duration_seconds": time.monotonic() - start, "reused": False, "review_status": "needs_independent_visual_review"}
        write_json(output / "receipt.json", receipt)
        return receipt
    except Exception as exc:
        write_json(output / "receipt.json", {"status": "failed", "pdf_sha256": pdf_sha, "config_sha256": config_sha,
                                            "duration_seconds": time.monotonic() - start, "error": str(exc)})
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", choices=("apple-vision", "tesseract"), required=True)
    parser.add_argument("--language", action="append", required=True, help="Repeat for languages; Vision id-ID, Tesseract ind (must be installed)")
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(generate(args.pdf, args.output, args.engine, args.language, args.dpi), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
