"""Recreate the initial allowlisted extraction scaffold from a verified Git commit.

Developer provenance tool, not needed to run/install the standalone application.
Reads only committed Git objects, never the source working tree or runtime data.
Usage: python scripts/export_backend.py --source PATH_TO_SOURCE_REPOSITORY
This does not reconstruct subsequent standalone security/portability adaptations.
Do not rerun over later standalone edits without reviewing the resulting diff and
running the complete tests; the application itself never invokes this script.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import symtable

COMMIT = "4d8ef239519d93bbb33be06f8f3a319df354c236"
TARGET = Path(__file__).resolve().parents[1]
ROOT_FUNCTIONS = {
    "modelark_status", "ai_providers_status", "list_finish_library", "upload_finish_library_item",
    "standardized_http_error", "standardized_internal_error", "list_assets", "update_asset", "delete_asset",
    "extract_asset", "create_interior_compose_job", "review_interior_compose", "create_interior_report",
    "get_interior_report", "get_interior_report_pdf", "get_job",
}
OVERRIDES = {"ensure_tmp_dir", "generate_ai_render", "path_to_url", "load_local_catalog", "request_log_middleware"}
CONSTANTS = {"INTERIOR_PLACEMENT_RULES", "REPORT_CATEGORY_LABELS", "REPORT_TARGET_LABELS"}
COPY_FILES = [
    "webapp/image_align.py", "webapp/interior_report.py", "webapp/composition_quality.py",
    "webapp/modelark.py", "webapp/openai_provider.py",
    "room-asset-composer/services/__init__.py", "room-asset-composer/services/asset_store.py",
    "room-asset-composer/services/ai_asset.py", "room-asset-composer/services/image_postprocess.py",
    "room-asset-composer/services/sam_auto.py", "room-asset-composer/services/sam_client.py",
    "room-asset-composer/services/bg_remove.py",
    "tests/test_interior_composer.py", "tests/test_composition_quality.py", "tests/test_modelark.py",
]

HEADER = '''"""Composer-only API extracted from verified source commit 4d8ef239.

See extraction_provenance.json for exact original function spans and hashes.
Bind to 127.0.0.1. This local application is not a public multi-user service.
"""
from __future__ import annotations
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE / ".env", override=False)
ASSETS_ROOT = BASE / "room-asset-composer"
ROOM_ASSET_COMPOSER = ASSETS_ROOT
if str(ROOM_ASSET_COMPOSER) not in sys.path:
    sys.path.insert(0, str(ROOM_ASSET_COMPOSER))
from services import asset_store
from webapp import db
from webapp.image_align import align_result_to_source, lock_composite_to_source
from webapp.interior_report import render_interior_report_pdf
from webapp.composition_quality import verify_composition
from webapp.modelark import ModelArkSettings, generate_image as modelark_generate_image, is_configured as modelark_is_configured
from webapp.openai_provider import OpenAISettings, generate_image as openai_generate_image, is_configured as openai_is_configured

WEBAPP_TMP = BASE / "webapp" / "tmp"
FINISH_LIBRARY_DIR = BASE / "webapp" / "data" / "finish_library"
PDF_OUTPUT_DIR = BASE / "output" / "pdf"
INTERIOR_COMPOSE_DIR = WEBAPP_TMP / "interior_compositions"
ASSET_EXTRACT_DIR = WEBAPP_TMP / "asset_extractions"
INTERIOR_REPORT_DIR = BASE / "webapp" / "data" / "interior_reports"
LOGS_DIR = BASE / "webapp" / "logs"
ASSETS_CSV = asset_store.ASSETS_CSV
ASSET_OUTPUTS_DIR = asset_store.OUTPUTS_DIR
ASSET_CSV_COLUMNS = asset_store.CSV_COLUMNS
JOB_STALE_SECONDS = 600
CONCEPT_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}
app = FastAPI(title="Interior Composer Local API")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("interior_composer")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOGS_DIR / "server.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
codex_image_lock = threading.Lock()  # Legacy lock name; no CLI/cache integration remains.
asset_csv_lock = threading.Lock()
interior_reports_lock = threading.Lock()
finish_library_lock = threading.Lock()
rembg_session_lock = threading.Lock()
rembg_session: Any = None
JOB_SLOTS = {"image": threading.BoundedSemaphore(2)}


def ensure_tmp_dir() -> None:
    for directory in (WEBAPP_TMP, INTERIOR_COMPOSE_DIR, ASSET_EXTRACT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_local_catalog(directory: Path, label: str) -> dict[str, Any]:
    path = directory / "catalog.json"
    if not path.is_file():
        return {"categories": [], "items": []}
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(catalog, dict):
            raise ValueError("Invalid catalog")
        return catalog
    except (OSError, ValueError) as exc:
        raise korean_http(500, f"{label} 카탈로그를 읽을 수 없습니다.") from exc


def path_to_url(path: Path) -> str:
    resolved = path.resolve()
    for directory, prefix in ((WEBAPP_TMP, "/webapp/tmp"), (ASSET_OUTPUTS_DIR, "/room-asset-composer/data/outputs"),
                              (FINISH_LIBRARY_DIR, "/webapp/data/finish_library"), (INTERIOR_REPORT_DIR, "/webapp/data/interior_reports")):
        if resolved.is_relative_to(directory.resolve()):
            return prefix + "/" + resolved.relative_to(directory.resolve()).as_posix()
    raise ValueError("공개 결과 이미지 경로만 URL로 변환할 수 있습니다.")


def generate_ai_render(prompt: str, image_paths: list[Path], output_path: Path, label: str,
                       log_parts: list[str], *, job_id: str | None = None,
                       quality: str = "standard", ai_mode: str = "flash") -> Path:
    """Preserve provider routing, but fail closed instead of reading local CLI credentials."""
    mode = normalize_ai_mode(ai_mode)
    configured = openai_is_configured() if mode == "premium" else modelark_is_configured()
    if not configured:
        missing = "OPENAI_API_KEY" if mode == "premium" else "ARK_API_KEY"
        raise ValueError(f"선택한 {mode} 공급자의 {missing}를 프로젝트 .env에 설정해 주세요.")
    with codex_image_lock:
        result = (openai_generate_image(prompt, image_paths, output_path=output_path) if mode == "premium" else
                  modelark_generate_image(prompt, image_paths, output_path=output_path, quality=quality))
    provider = "openai" if mode == "premium" else "modelark"
    log_parts.append(f"{provider} {result['model']} 생성 완료: {output_path.name}")
    if job_id:
        usage = safe_provider_usage(result.get("usage"))
        update_job(job_id, provider=provider, provider_model=result.get("model"), provider_usage=usage, provider_usage_available=bool(usage))
    return output_path


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "interior-composer"}


@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    logger.info("request method=%s path=%s status=%s elapsed_ms=%.1f", request.method, request.url.path,
                response.status_code, (time.perf_counter()-started)*1000)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
'''

FOOTER = '''

def image_file_response(root: Path, relative: str) -> FileResponse:
    """Never expose metadata, databases, source, env files, logs, or symlink escapes."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() not in CONCEPT_IMAGE_EXTENSIONS or not path.is_file():
        raise korean_http(404, "이미지를 찾을 수 없습니다.")
    return FileResponse(path, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@app.get("/room-asset-composer/data/outputs/{filename}")
def asset_image(filename: str):
    return image_file_response(ASSET_OUTPUTS_DIR, filename)


@app.get("/webapp/data/finish_library/{category}/{filename}")
def finish_image(category: str, filename: str):
    return image_file_response(FINISH_LIBRARY_DIR, f"{category}/{filename}")


@app.get("/webapp/data/interior_reports/{report_id}/{filename}")
def report_image(report_id: str, filename: str):
    report_id = normalized_interior_report_id(report_id)
    return image_file_response(INTERIOR_REPORT_DIR, f"{report_id}/{filename}")


@app.get("/webapp/tmp/{relative:path}")
def temporary_image(relative: str):
    # Only image files are served, even when metadata shares a result directory.
    return image_file_response(WEBAPP_TMP, relative)
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--refresh-generated", action="store_true", help="Explicitly overwrite only the generated allowlist; review standalone adaptations first")
    args = parser.parse_args()
    def source_file(path: str) -> str:
        return subprocess.check_output(["git", "-C", str(args.source), "show", f"{COMMIT}:{path}"]).decode("utf-8")
    source = source_file("webapp/server.py")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    function_tables = {table.get_name(): table for table in symtable.symtable(source, "server.py", "exec").get_children()}
    def global_names(table):
        names = {symbol.get_name() for symbol in table.get_symbols() if symbol.is_global()}
        for child in table.get_children():
            names.update(global_names(child))
        return names
    selected = set(ROOT_FUNCTIONS)
    while True:
        deps = {name for selected_name in selected for name in global_names(function_tables[selected_name])}
        added = (deps & functions.keys()) - selected - OVERRIDES
        if not added:
            break
        selected.update(added)
    spans = []
    pieces = []
    for node in tree.body:
        names = {node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else {
            target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)
        }
        if not names.intersection(selected | CONSTANTS):
            continue
        start = min([node.lineno] + [decorator.lineno for decorator in getattr(node, "decorator_list", [])])
        text = "\n".join(source.splitlines()[start - 1:node.end_lineno])
        pieces.append(text)
        spans.append({"name": sorted(names)[0], "start_line": start, "end_line": node.end_lineno,
                      "sha256": hashlib.sha256(text.encode()).hexdigest()})
    destination = TARGET / "webapp/server.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not args.refresh_generated:
        raise SystemExit("Refusing to overwrite existing extracted server; review and migrate manually.")
    destination.write_text(HEADER + "\n\n" + "\n\n\n".join(pieces) + FOOTER, encoding="utf-8")
    copied = []
    for path in COPY_FILES:
        target = TARGET / path
        if target.exists() and not args.refresh_generated:
            raise SystemExit(f"Refusing to overwrite {path}")
        text = source_file(path)
        original_hash = hashlib.sha256(text.encode()).hexdigest()
        if path in {"webapp/modelark.py", "webapp/openai_provider.py"}:
            text = text.replace('BASE / "webapp" / ".env"', 'BASE / ".env"').replace("webapp/.env", ".env")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        copied.append({"path": path, "source_sha256": original_hash})
    (TARGET / "webapp/extraction_provenance.json").write_text(json.dumps({
        "source_commit": COMMIT, "source_module": "webapp/server.py", "extracted_functions": spans,
        "copied_files": copied, "standalone_adaptations": [
            "Composer-only module dependencies and assets/jobs database",
            "Root .env and target-local runtime paths; no runtime data copied",
            "Missing provider fails explicitly; no local Codex CLI/cache fallback",
            "Public static paths restricted to images, reports PDF endpoint, and health endpoint",
        ],
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(spans)} Composer definitions and {len(copied)} allowlisted source/test files")


if __name__ == "__main__":
    main()
