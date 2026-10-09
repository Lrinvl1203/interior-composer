"""Composer-only API extracted from verified source commit 4d8ef239.

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
from urllib.parse import urlsplit
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE / ".env", override=False)
os.environ.setdefault("U2NET_HOME", str(BASE / ".cache" / "rembg"))
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


class ProviderNotConfiguredError(ValueError):
    """Known local configuration error whose generated message is safe for the UI."""

    def __init__(self, ai_mode: str):
        mode = normalize_ai_mode(ai_mode)
        missing = "OPENAI_API_KEY" if mode == "premium" else "ARK_API_KEY"
        super().__init__(f"선택한 {mode} 공급자의 {missing}를 프로젝트 .env에 설정해 주세요.")


def generate_ai_render(prompt: str, image_paths: list[Path], output_path: Path, label: str,
                       log_parts: list[str], *, job_id: str | None = None,
                       quality: str = "standard", ai_mode: str = "flash") -> Path:
    """Preserve provider routing, but fail closed instead of reading local CLI credentials."""
    mode = normalize_ai_mode(ai_mode)
    configured = openai_is_configured() if mode == "premium" else modelark_is_configured()
    if not configured:
        raise ProviderNotConfiguredError(mode)
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
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not mutation_origin_allowed(request):
        message = "로컬 Composer 화면에서 보낸 변경 요청만 허용됩니다."
        return JSONResponse(status_code=403, content={"detail": message, "error": {"code": "ORIGIN_FORBIDDEN", "message": message}})
    started = time.perf_counter()
    response = await call_next(request)
    logger.info("request method=%s path=%s status=%s elapsed_ms=%.1f", request.method, request.url.path,
                response.status_code, (time.perf_counter()-started)*1000)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def loopback_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        return f"http://{parsed.hostname}:{parsed.port or 80}"
    except ValueError:
        return None


def mutation_origin_allowed(request: Request) -> bool:
    # Cross-site browser requests must not trigger uploads or paid generations.
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True  # Local CLI and same-origin Next server proxy have no Origin.
    normalized = loopback_origin(origin)
    if normalized is None:
        return False
    allowed = set()
    for name, default in (("INTERIOR_FRONTEND_PORT", 3100), ("INTERIOR_BACKEND_PORT", 18877)):
        try:
            port = int(os.getenv(name, str(default)))
        except ValueError:
            port = default
        if 1 <= port <= 65535:
            allowed.update({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})
    direct_api = loopback_origin(f"{request.url.scheme}://{request.url.netloc}")
    if direct_api:
        allowed.add(direct_api)
    return normalized in allowed


@app.get("/api/modelark/status")
def modelark_status() -> dict[str, Any]:
    """Expose active model IDs without ever returning the API key."""

    return ModelArkSettings.from_env(required=False).public_summary()


@app.get("/api/ai-providers/status")
def ai_providers_status() -> dict[str, Any]:
    """Return picker metadata without exposing either provider's API key."""

    return {
        "default_mode": "flash",
        "modes": {
            "flash": {
                "label": "Flash",
                "provider": "BytePlus ModelArk",
                **ModelArkSettings.from_env(required=False).public_summary(),
            },
            "premium": {
                "label": "Premium",
                "provider": "OpenAI",
                **OpenAISettings.from_env(required=False).public_summary(),
            },
        },
    }


def normalize_ai_mode(value: Any) -> str:
    mode = str(value or "flash").strip().lower()
    if mode not in {"flash", "premium"}:
        raise ValueError("AI 모드는 flash 또는 premium이어야 합니다.")
    return mode


def local_image_url(directory: Path, relative_path: Path) -> str | None:
    """Return only images that have finished generating; missing images are normal.
    저장 용량을 위해 원본 PNG를 WebP로 변환해 두므로 .webp를 우선 찾는다."""

    root = directory.resolve()
    for candidate in (relative_path.with_suffix(".webp"), relative_path):
        image_path = (directory / candidate).resolve()
        if image_path.is_relative_to(root) and image_path.is_file():
            return f"/webapp/data/{directory.name}/{candidate.as_posix()}"
    return None


def finish_library_data() -> dict[str, Any]:
    catalog = load_local_catalog(FINISH_LIBRARY_DIR, "마감재")
    categories = [entry for entry in catalog.get("categories", []) if isinstance(entry, dict)]
    category_keys = {str(entry.get("key", "")) for entry in categories if str(entry.get("key", ""))}
    items: list[dict[str, Any]] = []
    for raw_item in catalog.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        category = str(item.get("category", ""))
        item_id = str(item.get("id", ""))
        if not category or not item_id or category not in category_keys:
            continue
        item["image_url"] = local_image_url(FINISH_LIBRARY_DIR, Path(category) / f"{item_id}.png")
        items.append(item)
    return {"categories": categories, "items": items}


def valid_finish_selections(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise korean_http(400, "마감재 선택값은 객체여야 합니다.")
    catalog = finish_library_data()
    categories = {str(entry.get("key")) for entry in catalog["categories"]}
    items = {
        str(item.get("id")): str(item.get("category"))
        for item in catalog["items"]
    }
    selections: dict[str, str] = {}
    for category, item_id in value.items():
        category_key = str(category)
        if category_key not in categories or not isinstance(item_id, str) or not item_id.strip():
            raise korean_http(400, "유효하지 않은 마감재 선택입니다.")
        if items.get(item_id) != category_key:
            raise korean_http(400, "카탈로그에 없는 마감재입니다.")
        selections[category_key] = item_id
    return selections


@app.get("/api/finish-library")
def list_finish_library() -> dict[str, Any]:
    return finish_library_data()


@app.post("/api/finish-library/upload")
def upload_finish_library_item(
    file: UploadFile = File(...),
    name: str = Form(""),
    category: str = Form(...),
    tone: str = Form(""),
    note: str = Form(""),
) -> dict[str, Any]:
    """상담 중 확보한 스와치를 마감재 앨범에 추가한다 — 내부 툴이라 전 프로젝트 공용."""
    if file.content_type and not file.content_type.startswith("image/"):
        raise korean_http(400, "이미지 파일만 마감재 스와치로 업로드할 수 있습니다.")
    with finish_library_lock:
        catalog = load_local_catalog(FINISH_LIBRARY_DIR, "마감재")
        category_keys = {str(entry.get("key")) for entry in catalog.get("categories", []) if isinstance(entry, dict)}
        key = category.strip()
        if key not in category_keys:
            raise korean_http(400, f"알 수 없는 마감재 분류입니다: {key or '(빈 값)'}")
        try:
            image = ImageOps.exif_transpose(load_uploaded_image(file)).convert("RGB")
        except (ValueError, OSError, UnidentifiedImageError) as exc:
            raise korean_http(400, f"스와치 이미지를 읽을 수 없습니다: {exc}") from exc
        image.thumbnail((1200, 1200))
        item_id = f"{key[:2]}-custom-{uuid.uuid4().hex[:6]}"
        target_dir = FINISH_LIBRARY_DIR / key
        target_dir.mkdir(parents=True, exist_ok=True)
        image.save(target_dir / f"{item_id}.webp", "WEBP", quality=88)
        item: dict[str, Any] = {"id": item_id, "category": key, "name": name.strip() or Path(file.filename or "").stem or "업로드 마감재", "tier": "커스텀"}
        if tone.strip():
            item["tone"] = tone.strip()
        if note.strip():
            item["note"] = note.strip()
        items = catalog.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            catalog["items"] = items
        items.append(item)
        (FINISH_LIBRARY_DIR / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**item, "image_url": local_image_url(FINISH_LIBRARY_DIR, Path(key) / f"{item_id}.png")}


def korean_http(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


@app.exception_handler(HTTPException)
async def standardized_http_error(_request:Request,exc:HTTPException):
    message=str(exc.detail);code={400:"BAD_REQUEST",404:"NOT_FOUND",409:"CONFLICT",413:"PAYLOAD_TOO_LARGE"}.get(exc.status_code,"HTTP_ERROR")
    return JSONResponse(status_code=exc.status_code,content={"detail":message,"error":{"code":code,"message":message}})


@app.exception_handler(Exception)
async def standardized_internal_error(request:Request,exc:Exception):
    logger.exception("unhandled path=%s",request.url.path)
    return JSONResponse(status_code=500,content={"detail":"서버 내부 오류가 발생했습니다.","error":{"code":"INTERNAL_ERROR","message":"서버 내부 오류가 발생했습니다."}})


def project_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_provider_usage(value: Any) -> dict[str, int]:
    """Keep only provider-reported counters, never arbitrary response metadata."""
    if not isinstance(value, dict):
        return {}
    allowed = {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "image_tokens", "images_generated"}
    return {key: count for key, count in value.items() if key in allowed and type(count) is int and count >= 0}


def output_file_from_row(row: dict[str, str]) -> str:
    output_filename = str(row.get("output_filename", "")).strip()
    if output_filename:
        return output_filename
    output_path = str(row.get("output_path", "")).strip()
    return output_path.replace("\\", "/").rstrip("/").split("/")[-1]


def read_asset_rows() -> list[dict[str, str]]:
    stored = db.list_assets()
    if stored:
        return [{column: str(row.get(column, "")) for column in ASSET_CSV_COLUMNS} for row in stored]
    if not ASSETS_CSV.exists():
        return []  # A new standalone installation intentionally starts empty.

    try:
        with ASSETS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        raise korean_http(500, f"에셋 CSV를 읽을 수 없습니다: {exc}") from exc


def write_asset_rows_atomically(rows: list[dict[str, str]]) -> None:
    temporary_path: Path | None = None
    try:
        ASSETS_CSV.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=ASSETS_CSV.parent,
            prefix=f".{ASSETS_CSV.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=ASSET_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(
                {
                    column: str(row.get(column, ""))
                    for column in ASSET_CSV_COLUMNS
                }
                for row in rows
            )
        os.replace(temporary_path, ASSETS_CSV)
        db.replace_assets(rows)
    except OSError as exc:
        raise korean_http(500, f"에셋 CSV를 갱신할 수 없습니다: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def asset_response(row: dict[str, str]) -> dict[str, str]:
    output_file = output_file_from_row(row)
    response = {
        "asset_id": str(row.get("asset_id", "")),
        "asset_name": str(row.get("asset_name", "")),
        "category": str(row.get("category", "")),
        "tags": str(row.get("tags", "")),
        "url": f"/room-asset-composer/data/outputs/{output_file}",
    }
    for field in ("project_id", "provenance", "width_mm", "depth_mm", "height_mm", "anchor_point", "default_scale", "orientation_deg", "version", "parent_asset_id", "archived", "history_json", "thumbnail_path"):
        response[field] = str(row.get(field, ""))
    response["composer_url"] = f"/composer-editor/editor.html?asset={row.get('asset_id', '')}"
    return response


def tail_text(text: str, lines: int = 40) -> str:
    pieces = text.splitlines()
    if not pieces:
        return ""
    return "\n".join(pieces[-lines:])


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def parse_prompt_json(prompt: str) -> Any:
    try:
        return json.loads(prompt or "null")
    except json.JSONDecodeError as exc:
        raise ValueError("프롬프트 JSON 형식이 올바르지 않습니다.") from exc


def parse_box_prompt(prompt: str) -> tuple[int, int, int, int]:
    payload = parse_prompt_json(prompt)
    box = payload.get("box") if isinstance(payload, dict) else payload
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("박스 프롬프트는 [x0,y0,x1,y1] 형식이어야 합니다.")
    try:
        return tuple(int(round(float(value))) for value in box)  # type: ignore[return-value]
    except (TypeError, ValueError):
        raise ValueError("박스 프롬프트 값은 숫자여야 합니다.") from None


def parse_points_prompt(prompt: str) -> list[tuple[int, int]]:
    payload = parse_prompt_json(prompt)
    points = payload.get("points") if isinstance(payload, dict) else payload
    if not isinstance(points, list) or not points:
        raise ValueError("포인트 프롬프트는 [[x,y],...] 형식이어야 합니다.")
    parsed: list[tuple[int, int]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("포인트 프롬프트는 [[x,y],...] 형식이어야 합니다.")
        try:
            parsed.append((int(round(float(point[0]))), int(round(float(point[1])))))
        except (TypeError, ValueError):
            raise ValueError("포인트 프롬프트 값은 숫자여야 합니다.") from None
    return parsed


def load_uploaded_image(file: UploadFile) -> Image.Image:
    try:
        file.file.seek(0, os.SEEK_END)
        if file.file.tell() > 25 * 1024 * 1024:
            raise ValueError("이미지는 25MB 이하여야 합니다.")
        file.file.seek(0)
        image = Image.open(file.file)
        if image.width * image.height > 40_000_000:
            raise ValueError("이미지는 4천만 픽셀 이하여야 합니다.")
        image.load()
        return image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("이미지 파일을 읽을 수 없습니다.") from exc


def get_rembg_session() -> Any:
    global rembg_session
    if rembg_session is None:
        with rembg_session_lock:
            if rembg_session is None:
                from services import bg_remove

                rembg_session = bg_remove.create_session()
    return rembg_session


def save_asset_with_method(
    image: Image.Image,
    asset_name: str,
    category: str,
    tags: str,
    original_filename: str,
    method: str,
) -> dict[str, Any]:
    from services import asset_store

    row = asset_store.save_asset(
        image,
        asset_name=asset_name,
        category=category,
        tags=tags,
        original_filename=original_filename,
    )
    if method != row.get("method"):
        with asset_csv_lock:
            assets = asset_store.read_assets()
            assets.loc[assets["asset_id"] == row["asset_id"], "method"] = method
            assets.to_csv(asset_store.ASSETS_CSV, index=False, encoding="utf-8-sig")
        row["method"] = method
    return row


def update_job(job_id: str, **updates: Any) -> None:
    with jobs_lock:
        if job_id in jobs:
            updates["updated_at"] = project_timestamp()
            jobs[job_id].update(updates)
            db.put_job(job_id, jobs[job_id])


def create_job(stale_after_s: int | None = None) -> str:
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "stage": "queued",
            "stage_label": "대기 중...",
            "warnings": [],
            "log_tail": "",
            "created_at": project_timestamp(),
            "updated_at": project_timestamp(),
        }
        if stale_after_s is not None:
            jobs[job_id]["stale_after_s"] = stale_after_s
        db.put_job(job_id, jobs[job_id])
    return job_id


def job_slot_runner(category: str | None, job_id: str, target, *args: Any) -> None:
    """잡 스레드 본체 — 카테고리 슬롯을 얻은 뒤 실제 워커를 실행한다.

    슬롯이 차 있으면 잡을 '대기 중'으로 표시하고 15초마다 updated_at을
    갱신해 stale 모니터가 대기를 멈춤으로 오판하지 않게 한다. category가
    None이거나 미등록이면 즉시 실행한다.
    """
    slot = JOB_SLOTS.get(category or "")
    if slot is not None and not slot.acquire(blocking=False):
        update_job(job_id, stage_label="앞선 작업이 끝나기를 기다리는 중...")
        while not slot.acquire(timeout=15):
            update_job(job_id)
    try:
        target(job_id, *args)
    finally:
        if slot is not None:
            slot.release()


def start_job_worker(category: str | None, job_id: str, target, *args: Any) -> None:
    # 실제 워커와 인자를 Thread args로 그대로 노출한다(테스트가 검사하는 계약).
    threading.Thread(target=job_slot_runner, args=(category, job_id, target, *args), daemon=True).start()


def finish_job_error(job_id: str, exc: BaseException, warnings: list[str], log: str) -> None:
    kind = type(exc).__name__
    safe_kind = kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", kind) else "ProviderError"
    configured_error = isinstance(exc, ProviderNotConfiguredError)
    message = str(exc) if configured_error else (
        f"작업을 완료하지 못했습니다. 공급자 설정과 네트워크 연결을 확인한 뒤 다시 시도해 주세요. ({safe_kind})"
    )
    # Both error and log_tail are browser-visible. Do not retain arbitrary provider
    # response text, signed URLs, credentials, local paths or a raw caller log.
    logger.error("job_failed job_id=%s error_type=%s", job_id, safe_kind)
    update_job(
        job_id,
        status="error",
        stage="error",
        stage_label="공급자 설정 필요" if configured_error else "작업 실패",
        error=message,
        error_code="PROVIDER_NOT_CONFIGURED" if configured_error else "GENERATION_FAILED",
        retryable=True,
        ready_for_report=False,
        warnings=warnings,
        log_tail=message,
    )


INTERIOR_PLACEMENT_RULES = (
    "Plan the room for function and visual balance before styling it.",
    "Keep doors, windows, fixed joinery, openings, and primary circulation completely clear.",
    "Use the architecture and the strongest existing focal point to organize furniture orientation.",
    "Respect human scale, believable clearances, support, floor contact, and conversational groupings.",
    "Prefer a few correctly scaled pieces and useful negative space over filling every empty area.",
    "Keep rugs and tables proportionate to their furniture group and preserve comfortable reach distances.",
    "Test the selected furniture as a complete plan; never invent a missing piece to make the composition fuller.",
)


def build_interior_compose_prompt(
    assets: list[dict[str, Any]],
    finishes: list[dict[str, Any]],
    finish_targets: dict[str, str],
    placements: list[dict[str, Any]],
    user_request: str,
) -> str:
    placement_by_asset = {
        str(item.get("asset_id")): item
        for item in placements
        if isinstance(item, dict) and item.get("asset_id")
    }
    asset_lines: list[str] = []
    image_index = 2
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "")
        name = str(asset.get("asset_name") or asset_id)
        placement = placement_by_asset.get(asset_id)
        if placement:
            position = (
                f"user-specified normalized frame box x={float(placement['x']):.3f}, "
                f"y={float(placement['y']):.3f}, width={float(placement['width']):.3f}, "
                f"height={float(placement['height']):.3f}"
            )
        else:
            position = "automatic optimal placement using the placement rules below"
        asset_lines.append(
            f"- Input image {image_index}: asset {name!r} (id {asset_id}); place exactly once; {position}."
        )
        image_index += 1

    finish_lines: list[str] = []
    for finish in finishes:
        category = str(finish.get("category") or "")
        target = finish_targets.get(category, "auto")
        finish_lines.append(
            f"- Input image {image_index}: finish {finish.get('name')!r}; apply only to target {target!r}; "
            "match real-world pattern scale, grain direction, joints, perspective, roughness, reflections, and source shading."
        )
        image_index += 1

    if assets:
        furniture_edit_rule = (
            "- Remove existing loose furniture and movable decor, repairing only the newly exposed wall and floor pixels. "
            "Never remove or redesign fixed architectural elements."
        )
        asset_rules = f"""{chr(10).join(asset_lines)}
- The listed assets are the complete and exclusive furniture inventory. Use every listed asset exactly once.
- Do not duplicate, omit, merge, redesign, recolor, substitute, or add furniture, lighting, plants, artwork, rugs, accessories, or decor that is not listed.
- Generate a natural new viewing angle for each asset so it matches input image 1, while preserving its silhouette, proportions, construction, color, material, legs, handles, seams, and distinctive details."""
        placement_rules = f"""{chr(10).join(f'- {rule}' for rule in INTERIOR_PLACEMENT_RULES)}
- User-specified boxes always override automatic placement."""
        output_qa = "Confirm internally that each listed asset appears exactly once, no unlisted object was added, every assigned finish stayed on its target, and the BEFORE camera/composition is unchanged."
    else:
        furniture_edit_rule = (
            "- FINISH-ONLY EDIT: Preserve every existing piece of furniture, lighting, plant, artwork, rug, accessory, "
            "movable decor, and its exact position, scale, orientation, appearance, shadows, and occlusion. "
            "Do not remove, replace, rearrange, redesign, or add any object."
        )
        asset_rules = """- No furniture asset is selected or supplied.
- Preserve the existing furniture and decor inventory exactly as it appears in input image 1.
- Do not add, remove, replace, move, rotate, resize, duplicate, recolor, or redesign any furniture or decor."""
        placement_rules = "- No asset placement is allowed. Only the selected finish surfaces may change."
        output_qa = "Confirm internally that every existing object remains unchanged, no object was added or removed, every assigned finish stayed on its target, and the BEFORE camera/composition is unchanged."

    return f"""Edit input image 1 as the immutable BEFORE frame for a constrained interior composition.

NON-NEGOTIABLE CAMERA AND STRUCTURE LOCK:
- Preserve the exact canvas dimensions, crop, camera position, lens, perspective, vanishing points, horizon, room envelope, wall edges, ceiling, floor boundaries, windows, doors, openings, and fixed joinery from input image 1.
- This is a localized image edit, not a new room generation. Pixels outside furniture replacement, selected finish surfaces, contact shadows, reflections, and necessary occlusion edges should remain visually unchanged.
{furniture_edit_rule}

ASSET IDENTITY AND COUNT LOCK:
{asset_rules}

FINISH ASSIGNMENTS:
{chr(10).join(finish_lines) if finish_lines else '- Keep all existing finishes unchanged.'}
- Apply finishes only to their assigned surfaces. Preserve surface boundaries, original lighting gradients, shadows, and architectural detail.

AUTOMATIC PLACEMENT RULES FOR ASSETS WITHOUT A USER BOX:
{placement_rules}

LIGHTING AND COMPOSITING:
- Match source light direction, exposure, white balance, depth, occlusion, contact shadows, cast shadows, and reflections.
- Every object must sit on a plausible support surface and must not float, intersect a wall, block a door, or break circulation.

USER NOTE:
{user_request.strip() or 'No additional preference. Follow the locked rules exactly.'}

OUTPUT QA BEFORE RETURNING:
{output_qa} Return only one clean AFTER image with no labels, borders, split screen, watermark, or explanatory text."""


def interior_compose_worker(
    job_id: str,
    source_path: Path,
    assets: list[dict[str, Any]],
    finishes: list[dict[str, Any]],
    finish_targets: dict[str, str],
    placements: list[dict[str, Any]],
    user_request: str,
    ai_mode: str,
) -> None:
    log_parts: list[str] = []
    warnings: list[str] = []
    job_root = INTERIOR_COMPOSE_DIR / job_id[:8]
    result_path = job_root / "after.png"
    prompt = build_interior_compose_prompt(assets, finishes, finish_targets, placements, user_request)
    try:
        job_root.mkdir(parents=True, exist_ok=True)
        (job_root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        update_job(job_id, status="running", stage="composing", stage_label="가구와 마감재를 부분 합성하는 중")
        inputs = [source_path]
        inputs.extend(Path(item["path"]) for item in assets)
        inputs.extend(Path(item["path"]) for item in finishes)
        snapshot = get_job(job_id).get("composition_snapshot") or {}
        quality_review: dict[str, Any] = {"status": "unverified", "issues": ["verification_not_run"]}
        pixel_lock: dict[str, object] = {}
        provider_calls: list[dict[str, Any]] = []
        quality_calls: list[dict[str, Any]] = []
        attempts = 0
        while attempts < 2:
            attempts += 1
            raw_path = job_root / f"after-raw-attempt-{attempts}.png"
            attempt_prompt = prompt
            if attempts == 2:
                attempt_prompt += "\n\nMANDATORY CORRECTION OF PREVIOUS RESULT:\n" + str(quality_review["repair_instruction"])
            provider_call: dict[str, Any] = {
                "attempt": attempts, "requested_ai_mode": ai_mode,
                "status": "dispatched", "provider": None, "model": None,
                "usage": {}, "usage_available": False,
            }
            provider_calls.append(provider_call)
            # Clear previous-attempt summary before dispatch: a failed repair
            # must not inherit the successful first call's reported usage.
            update_job(job_id, provider=None, provider_model=None, provider_usage={},
                       provider_usage_available=False, provider_calls=provider_calls,
                       quality_calls=quality_calls, ready_for_report=False)
            try:
                generate_ai_render(
                    attempt_prompt, inputs, raw_path, "인테리어 부분 합성", log_parts,
                    ai_mode=ai_mode, job_id=job_id,
                    quality="pro" if ai_mode == "premium" else "standard",
                )
            except Exception:
                provider_call["status"] = "failed_or_unknown"
                update_job(job_id, provider_calls=provider_calls, quality_calls=quality_calls,
                           provider=None, provider_model=None, provider_usage={},
                           provider_usage_available=False, ready_for_report=False)
                raise
            provider_job = get_job(job_id)
            provider_call.update({
                "status": "completed", "provider": provider_job.get("provider"),
                "model": provider_job.get("provider_model"),
                "usage": provider_job.get("provider_usage") or {},
                "usage_available": bool(provider_job.get("provider_usage_available")),
                "raw_sha256": file_sha256(raw_path),
            })
            update_job(job_id, provider_calls=provider_calls)
            shutil.copy2(raw_path, result_path)
            pixel_lock = {}
            try:
                update_job(job_id, stage="pixel_lock", stage_label="Before 좌표계 고정 검사 중")
                pixel_lock = lock_composite_to_source(source_path, result_path)
            except Exception as align_exc:
                warnings.append(f"pixel_lock_failed:{align_exc.__class__.__name__}")
                # Resizing is not proof of camera/structural alignment.
                align_result_to_source(source_path, result_path)
            update_job(job_id, stage="quality_review", stage_label="실제 결과 이미지 품질 검증 중")
            try:
                quality_review = verify_composition(source_path, result_path, assets, snapshot)
            except Exception as verify_exc:
                logger.exception("composition verification failed job_id=%s", job_id)
                quality_review = {
                    "status": "unverified", "method": "verification_error",
                    "issues": [f"verification_error:{verify_exc.__class__.__name__}"],
                    "repair_instruction": "", "provider_invoked": False,
                    "model": None, "usage": {}, "duration_seconds": 0,
                }
            quality_review["applied_to"] = "delivered_after"
            quality_review["result_sha256"] = file_sha256(result_path)
            quality_calls.append({
                "attempt": attempts, "status": "completed",
                "provider_invoked": bool(quality_review.get("provider_invoked")),
                "model": quality_review.get("model"), "usage": quality_review.get("usage") or {},
                "result_sha256": quality_review["result_sha256"],
                "duration_seconds": quality_review.get("duration_seconds"),
            })
            update_job(job_id, provider_calls=provider_calls, quality_calls=quality_calls,
                       quality_review=quality_review, result_revision=attempts,
                       result_sha256=quality_review["result_sha256"], ready_for_report=False)
            if quality_review.get("status") != "failed" or not quality_review.get("repair_instruction"):
                break
        result_sha256 = file_sha256(result_path)
        frame_aligned = bool(pixel_lock.get("pixel_coordinate_locked"))
        ready_for_report = frame_aligned and quality_review.get("status") == "passed"
        update_job(
            job_id,
            status="done",
            stage="complete",
            stage_label="합성 및 검증 완료" if ready_for_report else "합성 완료 · 검토 또는 수정 필요",
            source_url=path_to_url(source_path),
            result_url=path_to_url(result_path),
            asset_ids=[str(item.get("asset_id") or "") for item in assets],
            finish_ids=[str(item.get("id") or "") for item in finishes],
            placement_count=len(placements),
            frame_aligned=frame_aligned,
            pixel_lock=pixel_lock,
            quality_review=quality_review,
            ready_for_report=ready_for_report,
            result_revision=attempts,
            result_sha256=result_sha256,
            provider_calls=provider_calls,
            quality_calls=quality_calls,
            warnings=warnings,
            log_tail=tail_text("\n".join(log_parts)),
        )
    except Exception as exc:
        finish_job_error(job_id, exc, warnings, "\n".join(log_parts))


@app.get("/api/assets")
def list_assets() -> list[dict[str, str]]:
    return [asset_response(row) for row in read_asset_rows() if row.get("archived", "false").lower() != "true"]


@app.patch("/api/assets/{asset_id}")
def update_asset(asset_id: str, payload: dict[str, Any]) -> dict[str, str]:
    editable_fields = {"asset_name", "category", "tags", "width_mm", "depth_mm", "height_mm", "anchor_point", "default_scale", "orientation_deg"}
    requested_fields = editable_fields.intersection(payload)
    if not requested_fields:
        raise korean_http(400, "수정할 에셋 정보가 없습니다.")
    numeric_fields = {"width_mm", "depth_mm", "height_mm", "default_scale", "orientation_deg"}
    for field in requested_fields & numeric_fields:
        try:
            value = float(payload[field])
        except (TypeError, ValueError):
            raise korean_http(400, f"{field} must be numeric") from None
        if field != "orientation_deg" and value <= 0:
            raise korean_http(400, f"{field} must be positive")
        payload[field] = str(value)
    if any(field not in numeric_fields and not isinstance(payload[field], str) for field in requested_fields):
        raise korean_http(400, "에셋 이름, 카테고리, 태그는 문자열이어야 합니다.")

    with asset_csv_lock:
        rows = read_asset_rows()
        row = next((item for item in rows if item.get("asset_id") == asset_id), None)
        if row is None:
            raise korean_http(404, "에셋을 찾을 수 없습니다.")
        for field in requested_fields:
            row[field] = payload[field]
        write_asset_rows_atomically(rows)
        return asset_response(row)


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str) -> dict[str, str]:
    with asset_csv_lock:
        rows = read_asset_rows()
        row_index = next(
            (index for index, row in enumerate(rows) if row.get("asset_id") == asset_id),
            None,
        )
        if row_index is None:
            raise korean_http(404, "에셋을 찾을 수 없습니다.")

        row = rows[row_index]
        row["archived"] = "true"
        write_asset_rows_atomically(rows)
        output_file = output_file_from_row(row)
        if output_file and False:  # Archive preserves files for version/history recovery.
            try:
                (ASSET_OUTPUTS_DIR / Path(output_file).name).unlink(missing_ok=True)
            except OSError as exc:
                raise korean_http(500, f"에셋 출력 파일을 삭제할 수 없습니다: {exc}") from exc
    return {"asset_id": asset_id}


def ai_asset_extract_worker(
    job_id: str,
    source_path: Path,
    box: tuple[int, int, int, int],
    description: str,
    ai_mode: str,
    should_save: bool,
    asset_name: str,
    category: str,
    tags: str,
    original_filename: str,
) -> None:
    """Run slow generative extraction outside the upload request lifecycle."""
    try:
        from services import ai_asset, image_postprocess

        update_job(job_id, status="running", stage="ai_extract", stage_label="AI가 선택한 에셋을 분리하는 중")
        with Image.open(source_path) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGBA")
            source.load()
        api_key = (
            OpenAISettings.from_env(required=False).api_key
            if ai_mode == "premium"
            else ModelArkSettings.from_env(required=False).api_key
        )
        extraction_quality = "high" if ai_mode == "premium" else "medium"
        extracted = ai_asset.extract_selected_asset(
            source,
            description,
            api_key,
            quality=extraction_quality,
            bbox=box,
            ai_mode=ai_mode,
        )
        metadata = extracted.info.get("provider_metadata") if isinstance(extracted.info, dict) else None
        if (isinstance(metadata, dict) and metadata.get("provider") in {"openai", "modelark"}
                and isinstance(metadata.get("model"), str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", metadata["model"])):
            provider_name, provider_model = metadata["provider"], metadata["model"]
            provider_usage = safe_provider_usage(metadata.get("usage"))
            usage_available = bool(metadata.get("usage_available")) and bool(provider_usage)
        else:
            # Older extractors and test doubles return only a PIL image.
            if ai_mode == "premium":
                provider_name, provider_model = "openai", ai_asset.MODEL
            else:
                settings = ModelArkSettings.from_env(required=False)
                provider_name = "modelark"
                provider_model = settings.image_pro_model if extraction_quality == "high" else settings.image_model
            provider_usage, usage_available = {}, False
        update_job(
            job_id, provider=provider_name, provider_model=provider_model,
            provider_usage=provider_usage, provider_usage_available=usage_available,
            provider_invoked=True,
        )
        final_image = image_postprocess.postprocess_image(extracted, auto_crop=True, feather_radius=1)
        if should_save:
            row = save_asset_with_method(
                final_image,
                asset_name=asset_name,
                category=category,
                tags=tags,
                original_filename=original_filename,
                method=f"ai_{ai_mode}",
            )
            update_job(
                job_id,
                status="done",
                stage="complete",
                stage_label="AI 에셋 추출 완료",
                asset_id=str(row["asset_id"]),
                result_url=f"/room-asset-composer/data/outputs/{row['output_filename']}",
            )
        else:
            result_path = ASSET_EXTRACT_DIR / f"{job_id}_result.png"
            final_image.convert("RGBA").save(result_path, format="PNG")
            update_job(
                job_id,
                status="done",
                stage="complete",
                stage_label="AI 에셋 추출 완료",
                result_url=path_to_url(result_path),
            )
    except Exception as exc:
        finish_job_error(job_id, exc, [], "")
        update_job(job_id, stage_label="AI 에셋 추출 실패")


@app.post("/api/extract")
def extract_asset(
    file: UploadFile = File(...),
    mode: str = Form("auto"),
    prompt: str = Form(""),
    description: str = Form(""),
    ai_mode: str = Form("premium"),
    save: str = Form("false"),
    asset_name: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),
) -> dict[str, str]:
    try:
        from services import image_postprocess

        image = load_uploaded_image(file)
        normalized_mode = mode.strip().lower()
        if normalized_mode == "auto":
            from services import bg_remove

            extracted = bg_remove.remove_background(image, session=get_rembg_session())
            method = "rembg"
        elif normalized_mode == "ai_box":
            if not description.strip():
                raise ValueError("선택한 에셋이 무엇인지 텍스트로 설명해 주세요.")
            if len(description) > 500:
                raise ValueError("에셋 설명은 500자 이내로 입력해 주세요.")
            selected_ai_mode = normalize_ai_mode(ai_mode)
            box = parse_box_prompt(prompt)
            ensure_tmp_dir()
            job_id = create_job(stale_after_s=600)
            source_path = ASSET_EXTRACT_DIR / f"{job_id}_source.png"
            ImageOps.exif_transpose(image).convert("RGBA").save(source_path, format="PNG")
            start_job_worker(
                "image",
                job_id,
                ai_asset_extract_worker,
                source_path,
                box,
                description.strip(),
                selected_ai_mode,
                parse_bool(save, default=False),
                asset_name,
                category,
                tags,
                file.filename or "upload.png",
            )
            return {"job_id": job_id, "status": "queued"}
        elif normalized_mode == "sam_box":
            from services import sam_client

            extracted = sam_client.extract_by_box(image, parse_box_prompt(prompt))
            method = "sam"
        elif normalized_mode == "sam_points":
            from services import sam_client

            extracted = sam_client.extract_by_click(image, parse_points_prompt(prompt))
            method = "sam"
        else:
            raise ValueError("mode는 auto, ai_box, sam_box, sam_points 중 하나여야 합니다.")

        final_image = image_postprocess.postprocess_image(
            extracted,
            auto_crop=True,
            feather_radius=1,
        )
        if parse_bool(save, default=False):
            row = save_asset_with_method(
                final_image,
                asset_name=asset_name,
                category=category,
                tags=tags,
                original_filename=file.filename or "upload.png",
                method=method,
            )
            return {
                "url": f"/room-asset-composer/data/outputs/{row['output_filename']}",
                "asset_id": str(row["asset_id"]),
            }

        ensure_tmp_dir()
        tmp_name = f"{uuid.uuid4().hex}.png"
        tmp_path = WEBAPP_TMP / tmp_name
        final_image.convert("RGBA").save(tmp_path, format="PNG")
        return {"url": f"/webapp/tmp/{tmp_name}"}
    except ValueError as exc:
        raise korean_http(400, str(exc)) from exc
    except Exception as exc:
        raise korean_http(500, f"에셋 추출 중 오류가 발생했습니다: {exc}") from exc


@app.post("/api/interior-compose")
def create_interior_compose_job(
    before: UploadFile = File(...),
    asset_ids: str = Form("[]"),
    finishes: str = Form("{}"),
    finish_targets: str = Form("{}"),
    placements: str = Form("[]"),
    request: str = Form(""),
    ai_mode: str = Form("premium"),
) -> dict[str, Any]:
    """Compose selected cutout assets and catalog finishes into one locked BEFORE frame."""

    if before.content_type and not before.content_type.startswith("image/"):
        raise korean_http(400, "Before 이미지는 이미지 파일이어야 합니다.")
    if len(request) > 3000:
        raise korean_http(400, "추가 요청은 3,000자 이내로 입력해 주세요.")
    try:
        parsed_asset_ids = json.loads(asset_ids)
        parsed_finishes = json.loads(finishes)
        parsed_targets = json.loads(finish_targets)
        parsed_placements = json.loads(placements)
    except json.JSONDecodeError as exc:
        raise korean_http(400, "합성 설정 JSON 형식이 올바르지 않습니다.") from exc

    if not isinstance(parsed_asset_ids, list) or not all(isinstance(item, str) for item in parsed_asset_ids):
        raise korean_http(400, "에셋 목록 형식이 올바르지 않습니다.")
    selected_ids = [item.strip() for item in parsed_asset_ids if item.strip()]
    finish_selection = valid_finish_selections(parsed_finishes) if parsed_finishes else {}
    if not selected_ids and not finish_selection:
        raise korean_http(400, "가구 에셋 또는 마감재를 한 개 이상 선택해 주세요.")
    if len(selected_ids) > 8:
        raise korean_http(400, "한 장면에는 가구 에셋을 최대 8개까지 배치할 수 있습니다.")
    if len(selected_ids) != len(set(selected_ids)):
        raise korean_http(400, "동일한 가구 에셋은 한 번만 선택할 수 있습니다.")

    rows_by_id = {
        str(row.get("asset_id") or ""): row
        for row in read_asset_rows()
        if str(row.get("archived", "false")).lower() != "true"
    }
    missing_ids = [asset_id for asset_id in selected_ids if asset_id not in rows_by_id]
    if missing_ids:
        raise korean_http(404, f"가구 에셋을 찾을 수 없습니다: {', '.join(missing_ids)}")
    asset_records: list[dict[str, Any]] = []
    for asset_id in selected_ids:
        row = rows_by_id[asset_id]
        path = (ASSET_OUTPUTS_DIR / Path(output_file_from_row(row)).name).resolve()
        if not path.is_relative_to(ASSET_OUTPUTS_DIR.resolve()) or not path.is_file():
            raise korean_http(404, f"가구 에셋 이미지가 없습니다: {asset_id}")
        asset_records.append({
            "asset_id": asset_id,
            "asset_name": str(row.get("asset_name") or asset_id),
            "category": str(row.get("category") or "furniture"),
            "path": path,
        })

    if not isinstance(parsed_targets, dict):
        raise korean_http(400, "마감재 적용 면 형식이 올바르지 않습니다.")
    allowed_targets = {"auto", "all_walls", "accent_wall", "floor", "ceiling", "door", "fixed_joinery", "backsplash"}
    target_selection: dict[str, str] = {}
    for category, target in parsed_targets.items():
        if category not in finish_selection or not isinstance(target, str) or target not in allowed_targets:
            raise korean_http(400, "마감재 적용 면 선택이 올바르지 않습니다.")
        target_selection[str(category)] = target

    catalog = finish_library_data()
    finish_by_id = {str(item.get("id") or ""): item for item in catalog["items"]}
    finish_records: list[dict[str, Any]] = []
    for category, item_id in finish_selection.items():
        item = finish_by_id[item_id]
        path = next(
            (
                candidate
                for suffix in (".webp", ".png", ".jpg", ".jpeg")
                if (candidate := (FINISH_LIBRARY_DIR / category / f"{item_id}{suffix}").resolve()).is_relative_to(FINISH_LIBRARY_DIR.resolve())
                and candidate.is_file()
            ),
            None,
        )
        if path is None:
            raise korean_http(404, f"마감재 이미지를 찾을 수 없습니다: {item_id}")
        finish_records.append({
            "id": item_id,
            "category": category,
            "name": str(item.get("name") or item_id),
            "path": path,
        })

    if not isinstance(parsed_placements, list):
        raise korean_http(400, "가구 배치 정보 형식이 올바르지 않습니다.")
    normalized_placements: list[dict[str, Any]] = []
    placed_ids: set[str] = set()
    for raw in parsed_placements:
        if not isinstance(raw, dict) or str(raw.get("asset_id") or "") not in selected_ids:
            raise korean_http(400, "선택하지 않은 가구의 배치 정보가 포함되어 있습니다.")
        asset_id = str(raw["asset_id"])
        if asset_id in placed_ids:
            raise korean_http(400, "동일 가구에는 하나의 배치 위치만 지정할 수 있습니다.")
        try:
            x = float(raw["x"]); y = float(raw["y"])
            width = float(raw["width"]); height = float(raw["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise korean_http(400, "가구 배치 좌표는 숫자여야 합니다.") from exc
        if not (0 <= x < 1 and 0 <= y < 1 and 0.05 <= width <= 0.8 and 0.05 <= height <= 0.8 and x + width <= 1.001 and y + height <= 1.001):
            raise korean_http(400, "가구 배치 박스가 Before 이미지 영역을 벗어났습니다.")
        normalized_placements.append({"asset_id": asset_id, "x": x, "y": y, "width": width, "height": height})
        placed_ids.add(asset_id)

    try:
        mode = normalize_ai_mode(ai_mode)
        source_image = ImageOps.exif_transpose(load_uploaded_image(before)).convert("RGBA")
    except ValueError as exc:
        raise korean_http(400, str(exc)) from exc

    job_id = create_job(stale_after_s=1200)
    job_root = INTERIOR_COMPOSE_DIR / job_id[:8]
    job_root.mkdir(parents=True, exist_ok=True)
    source_path = job_root / "before.png"
    source_image.save(source_path, format="PNG")
    # Freeze the exact inputs at submission; the mutable libraries and draft UI
    # must never be consulted later to describe this result.
    snapshot_assets: list[dict[str, Any]] = []
    for index, item in enumerate(asset_records, 1):
        original = Path(item["path"])
        frozen = job_root / f"input-asset-{index}{original.suffix.lower()}"
        shutil.copy2(original, frozen)
        item["path"] = frozen
        snapshot_assets.append({
            "asset_id": item["asset_id"], "name": item["asset_name"],
            "category": item["category"], "source_path": str(frozen),
            "sha256": file_sha256(frozen),
        })
    snapshot_finishes: list[dict[str, Any]] = []
    for index, item in enumerate(finish_records, 1):
        original = Path(item["path"])
        frozen = job_root / f"input-finish-{index}{original.suffix.lower()}"
        shutil.copy2(original, frozen)
        item["path"] = frozen
        snapshot_finishes.append({
            "id": item["id"], "name": item["name"], "category": item["category"],
            "target": target_selection.get(item["category"], "auto"),
            "source_path": str(frozen), "sha256": file_sha256(frozen),
        })
    composition_snapshot = {
        "assets": snapshot_assets, "finishes": snapshot_finishes,
        "finish_targets": dict(target_selection),
        "placements": [dict(item) for item in normalized_placements],
        "user_request": request, "ai_mode": mode,
        "source_sha256": file_sha256(source_path),
    }
    update_job(
        job_id,
        source_url=path_to_url(source_path),
        asset_ids=selected_ids,
        finish_ids=list(finish_selection.values()),
        placement_count=len(normalized_placements),
        ai_mode=mode,
        composition_snapshot=composition_snapshot,
        ready_for_report=False,
    )
    start_job_worker(
        "image",
        job_id,
        interior_compose_worker,
        source_path,
        asset_records,
        finish_records,
        target_selection,
        normalized_placements,
        request,
        mode,
    )
    return {
        "job_id": job_id,
        "ai_mode": mode,
        "asset_count": len(asset_records),
        "finish_count": len(finish_records),
        "placement_count": len(normalized_placements),
    }


REPORT_CATEGORY_LABELS = {
    "wallpaper": "벽지",
    "flooring": "장판·바닥재",
    "tile": "타일",
    "film": "필름",
}


REPORT_TARGET_LABELS = {
    "auto": "AI 자동 판단",
    "all_walls": "보이는 전체 벽",
    "accent_wall": "포인트 벽",
    "floor": "바닥",
    "ceiling": "천장",
    "door": "문",
    "fixed_joinery": "고정 가구·문틀",
    "backsplash": "주방·욕실 벽면",
}


@app.post("/api/interior-compose/{job_id}/review")
def review_interior_compose(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Manually approve only an unverifiable, frame-aligned current result."""
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise korean_http(404, "검토할 작업을 찾을 수 없습니다.")
    job = get_job(job_id)
    if job.get("status") != "done" or not isinstance(job.get("composition_snapshot"), dict):
        raise korean_http(409, "완료된 검증 가능 작업만 검토할 수 있습니다.")
    result_path = INTERIOR_COMPOSE_DIR / job_id[:8] / "after.png"
    if not result_path.is_file():
        raise korean_http(404, "검토할 After 이미지가 없습니다.")
    current_hash = file_sha256(result_path)
    if (str(payload.get("result_revision")) != str(job.get("result_revision"))
            or payload.get("result_sha256") != job.get("result_sha256")
            or current_hash != job.get("result_sha256")):
        raise korean_http(409, "결과 이미지가 변경되었습니다. 최신 결과를 다시 확인해 주세요.")
    quality = job.get("quality_review") or {}
    if quality.get("status") == "failed":
        raise korean_http(409, "자동 검증 실패 결과는 승인할 수 없습니다. 먼저 수정이 필요합니다.")
    if quality.get("status") != "unverified" or not job.get("frame_aligned"):
        raise korean_http(409, "수동 검토는 정렬된 미확인 결과에만 사용할 수 있습니다.")
    checks = {key: payload.get(key) for key in ("inventory", "structure", "finishes")}
    if any(value is not True for value in checks.values()):
        raise korean_http(400, "가구 개수·구조·마감재를 모두 직접 확인해 주세요.")
    with jobs_lock:
        stored = jobs.get(job_id) or db.get_job(job_id)
        if not stored or stored.get("result_sha256") != current_hash:
            raise korean_http(409, "결과가 변경되었습니다.")
        jobs[job_id] = stored
        jobs[job_id]["manual_visual_review"] = {
            "result_revision": job.get("result_revision"),
            "result_sha256": current_hash, "checks": checks,
            "approved_at": project_timestamp(),
        }
        jobs[job_id]["ready_for_report"] = True
        jobs[job_id]["updated_at"] = project_timestamp()
        db.put_job(job_id, jobs[job_id])
    return get_job(job_id)


def normalized_interior_report_id(report_id: str) -> str:
    value = str(report_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise korean_http(404, "상담 보고서를 찾을 수 없습니다.")
    return value


def read_interior_report(report_id: str) -> dict[str, Any]:
    value = normalized_interior_report_id(report_id)
    path = INTERIOR_REPORT_DIR / value / "report.json"
    if not path.is_file():
        raise korean_http(404, "상담 보고서를 찾을 수 없습니다.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise korean_http(500, "상담 보고서 데이터를 읽을 수 없습니다.") from exc
    if not isinstance(report, dict):
        raise korean_http(500, "상담 보고서 데이터 형식이 올바르지 않습니다.")
    return report


def find_interior_report_for_job(job_id: str, title: str, space_type: str) -> dict[str, Any] | None:
    if not INTERIOR_REPORT_DIR.is_dir():
        return None
    matches: list[dict[str, Any]] = []
    for path in INTERIOR_REPORT_DIR.glob("*/report.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        if (
            item.get("compose_job_id") == job_id
            and str(item.get("title") or "") == title
            and str(item.get("space_type") or "") == space_type
        ):
            matches.append(item)
    return max(matches, key=lambda item: str(item.get("created_at") or ""), default=None)


def interior_report_versions(series_id: str) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    if not INTERIOR_REPORT_DIR.is_dir():
        return versions
    for path in INTERIOR_REPORT_DIR.glob("*/report.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict) or item.get("series_id") != series_id:
            continue
        versions.append({
            "report_id": str(item.get("report_id") or ""),
            "version": int(item.get("version") or 1),
            "created_at": str(item.get("created_at") or ""),
            "title": str(item.get("title") or "인테리어 상담 보고서"),
        })
    return sorted(versions, key=lambda item: (item["version"], item["created_at"]))


def public_interior_report(report: dict[str, Any]) -> dict[str, Any]:
    report_id = str(report.get("report_id") or "")
    result = dict(report)
    result["versions"] = interior_report_versions(str(report.get("series_id") or ""))
    result["pdf_url"] = f"/api/interior-reports/{report_id}/pdf"
    result["share_url"] = f"/tools/interior-composer/report/{report_id}"
    return result


def interior_report_consultation(
    assets: list[dict[str, Any]],
    finishes: list[dict[str, Any]],
    request: str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    manual_count = sum(1 for item in assets if item.get("placement") == "manual")
    automatic_count = len(assets) - manual_count
    if assets and finishes:
        summary = f"선택한 에셋 {len(assets)}개와 마감재 {len(finishes)}개로 생성한 상담 이미지입니다."
    elif assets:
        summary = f"선택한 에셋 {len(assets)}개로 생성한 상담 이미지입니다."
    else:
        summary = f"선택한 마감재 {len(finishes)}개로 생성한 상담 이미지입니다."
    notes = ["Before 좌표계 정렬 검사를 통과했습니다." if verification.get("frame_aligned") else "Before 좌표계 정렬은 검증되지 않았습니다."]
    if assets:
        notes.append(f"요청된 수동 배치 {manual_count}개, 자동 배치 {automatic_count}개입니다.")
    else:
        notes.append("가구 에셋을 선택하지 않은 마감재 전용 요청입니다.")
    if finishes:
        assignments = ", ".join(
            f"{item.get('category_label') or item.get('category')} - {item.get('target_label') or item.get('target')}"
            for item in finishes
        )
        notes.append(f"요청된 마감재 적용 면: {assignments}.")
    else:
        notes.append("마감재 변경은 요청되지 않았습니다.")
    if request.strip():
        notes.append("추가 요청은 생성 입력에 포함되었습니다. 실제 반영 여부는 결과 이미지를 확인하세요.")
    if verification.get("quality_status") == "passed":
        notes.append("최종 이미지의 자동 의미 검증을 통과했습니다.")
    elif verification.get("manual_visual_review"):
        notes.append("자동 의미 검증은 미확인 상태였으며, 사용자가 최종 이미지를 직접 승인했습니다.")
    return {"summary": summary, "notes": notes}


@app.post("/api/interior-reports")
def create_interior_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one completed composer result as a versioned, shareable report."""

    job_id = str(payload.get("job_id") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise korean_http(400, "완료된 After 작업 ID가 필요합니다.")
    job = get_job(job_id)
    if job.get("status") != "done":
        raise korean_http(409, "After 합성이 완료된 후 보고서를 생성해 주세요.")
    snapshot = job.get("composition_snapshot")
    if not isinstance(snapshot, dict):
        raise korean_http(409, "기존 작업에는 생성 당시 입력 기록이 없어 적용 내용을 검증할 수 없습니다. 새로 생성해 주세요.")
    if not job.get("ready_for_report"):
        raise korean_http(409, "정렬과 최종 이미지 검증이 완료된 결과만 보고서로 만들 수 있습니다.")
    review_status = (job.get("quality_review") or {}).get("status")
    manual_review = job.get("manual_visual_review") or {}
    if (not job.get("frame_aligned") or
            (review_status != "passed" and not (review_status == "unverified" and manual_review.get("result_sha256") == job.get("result_sha256")))):
        raise korean_http(409, "검증되지 않았거나 실패한 결과는 보고서로 만들 수 없습니다.")
    job_root = (INTERIOR_COMPOSE_DIR / job_id[:8]).resolve()
    before_path = job_root / "before.png"
    after_path = job_root / "after.png"
    if not job_root.is_relative_to(INTERIOR_COMPOSE_DIR.resolve()) or not before_path.is_file() or not after_path.is_file():
        raise korean_http(404, "보고서에 사용할 Before/After 이미지를 찾을 수 없습니다.")
    if file_sha256(before_path) != snapshot.get("source_sha256") or file_sha256(after_path) != job.get("result_sha256"):
        raise korean_http(409, "저장된 입력 또는 결과 이미지가 변경되어 보고서를 만들 수 없습니다.")

    title = str(payload.get("title") or "Before / After 인테리어 상담 보고서").strip()[:120]
    space_type = str(payload.get("space_type") or "공간 미지정").strip()[:80]
    request = str(snapshot.get("user_request") or "").strip()[:3000]
    placement_ids = {
        str(item.get("asset_id") or "")
        for item in snapshot.get("placements", [])
        if isinstance(item, dict)
    }
    target_payload = snapshot.get("finish_targets") if isinstance(snapshot.get("finish_targets"), dict) else {}

    assets: list[dict[str, Any]] = []
    for item in snapshot.get("assets", []):
        asset_id = str(item.get("asset_id") or "")
        source = Path(str(item.get("source_path") or ""))
        if not source.is_file() or file_sha256(source) != item.get("sha256"):
            raise korean_http(409, f"생성 당시 에셋을 확인할 수 없습니다: {asset_id}")
        assets.append({
            "asset_id": asset_id,
            "name": str(item.get("name") or asset_id),
            "category": str(item.get("category") or "furniture"),
            "placement": "manual" if asset_id in placement_ids else "automatic",
            "source_path": str(source),
        })

    finishes: list[dict[str, Any]] = []
    for item in snapshot.get("finishes", []):
        finish_id = str(item.get("id") or "")
        category = str(item.get("category") or "")
        target = str(item.get("target") or target_payload.get(category) or "auto")
        if target not in REPORT_TARGET_LABELS:
            target = "auto"
        image_path = Path(str(item.get("source_path") or ""))
        if not image_path.is_file() or file_sha256(image_path) != item.get("sha256"):
            raise korean_http(409, f"생성 당시 마감재를 확인할 수 없습니다: {finish_id}")
        finishes.append({
            "id": finish_id,
            "name": str(item.get("name") or finish_id),
            "category": category,
            "category_label": REPORT_CATEGORY_LABELS.get(category, category or "마감"),
            "target": target,
            "target_label": REPORT_TARGET_LABELS[target],
            "source_path": str(image_path),
        })

    with interior_reports_lock:
        previous_id = str(payload.get("previous_report_id") or "").strip()
        if not previous_id:
            existing = find_interior_report_for_job(job_id, title or "Before / After 인테리어 상담 보고서", space_type or "공간 미지정")
            if existing:
                existing_id = str(existing.get("report_id") or "")
                existing_dir = INTERIOR_REPORT_DIR / existing_id
                existing_pdf = PDF_OUTPUT_DIR / f"interior-report-{existing_id}.pdf"
                if not existing_pdf.is_file() or existing_pdf.stat().st_size > 12 * 1024 * 1024:
                    render_interior_report_pdf(existing, existing_dir, existing_pdf)
                return public_interior_report(existing)
        if previous_id:
            previous = read_interior_report(previous_id)
            series_id = str(previous.get("series_id") or previous.get("report_id") or uuid.uuid4().hex)
            version = max((item["version"] for item in interior_report_versions(series_id)), default=0) + 1
        else:
            series_id = uuid.uuid4().hex
            version = 1

        report_id = uuid.uuid4().hex
        report_dir = INTERIOR_REPORT_DIR / report_id
        pdf_path = PDF_OUTPUT_DIR / f"interior-report-{report_id}.pdf"
        report_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(before_path, report_dir / "before.png")
            shutil.copy2(after_path, report_dir / "after.png")
            public_assets: list[dict[str, Any]] = []
            for index, item in enumerate(assets, 1):
                source = Path(str(item.pop("source_path")))
                suffix = source.suffix.lower() if source.suffix.lower() in CONCEPT_IMAGE_EXTENSIONS else ".png"
                filename = f"asset-{index}{suffix}"
                if source.is_file():
                    shutil.copy2(source, report_dir / filename)
                public_assets.append({
                    **item,
                    "image_url": f"/webapp/data/interior_reports/{report_id}/{filename}" if (report_dir / filename).is_file() else None,
                })
            public_finishes: list[dict[str, Any]] = []
            for index, item in enumerate(finishes, 1):
                source_value = str(item.pop("source_path"))
                source = Path(source_value) if source_value else None
                suffix = source.suffix.lower() if source and source.suffix.lower() in CONCEPT_IMAGE_EXTENSIONS else ".png"
                filename = f"finish-{index}{suffix}"
                if source and source.is_file():
                    shutil.copy2(source, report_dir / filename)
                public_finishes.append({
                    **item,
                    "image_url": f"/webapp/data/interior_reports/{report_id}/{filename}" if (report_dir / filename).is_file() else None,
                })

            report = {
                "report_id": report_id,
                "series_id": series_id,
                "version": version,
                "title": title or "Before / After 인테리어 상담 보고서",
                "space_type": space_type or "공간 미지정",
                "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                "compose_job_id": job_id,
                "ai_mode": str(snapshot.get("ai_mode") or "premium"),
                "before_url": f"/webapp/data/interior_reports/{report_id}/before.png",
                "after_url": f"/webapp/data/interior_reports/{report_id}/after.png",
                "assets": public_assets,
                "finishes": public_finishes,
                "request": request,
                "verification": {
                    "frame_aligned": bool(job.get("frame_aligned")),
                    "quality_status": (job.get("quality_review") or {}).get("status"),
                    "quality_method": (job.get("quality_review") or {}).get("method"),
                    "manual_visual_review": bool(job.get("manual_visual_review")),
                    "result_sha256": job.get("result_sha256"),
                },
                "consultation": interior_report_consultation(public_assets, public_finishes, request, {
                    "frame_aligned": bool(job.get("frame_aligned")),
                    "quality_status": (job.get("quality_review") or {}).get("status"),
                    "manual_visual_review": bool(job.get("manual_visual_review")),
                }),
                "disclaimer": "상담용 이미지 제안서이며 실제 시공 전 현장 실측과 자재 샘플 확인이 필요합니다.",
            }
            render_interior_report_pdf(report, report_dir, pdf_path)
            temporary = report_dir / "report.json.tmp"
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, report_dir / "report.json")
        except Exception as exc:
            shutil.rmtree(report_dir, ignore_errors=True)
            pdf_path.unlink(missing_ok=True)
            logger.exception("Interior report generation failed")
            raise korean_http(500, f"상담 보고서를 생성하지 못했습니다: {exc}") from exc

    return public_interior_report(report)


@app.get("/api/interior-reports/{report_id}")
def get_interior_report(report_id: str) -> dict[str, Any]:
    return public_interior_report(read_interior_report(report_id))


@app.get("/api/interior-reports/{report_id}/pdf")
def get_interior_report_pdf(report_id: str) -> FileResponse:
    value = normalized_interior_report_id(report_id)
    report = read_interior_report(value)
    path = PDF_OUTPUT_DIR / f"interior-report-{value}.pdf"
    if not path.is_file():
        raise korean_http(404, "상담 보고서 PDF를 찾을 수 없습니다.")
    filename = f"morrow-interior-report-v{int(report.get('version') or 1):02d}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise korean_http(404, "작업을 찾을 수 없습니다.")
    with jobs_lock:
        job = jobs.get(job_id) or db.get_job(job_id)
        if job is None:
            raise korean_http(404, "작업을 찾을 수 없습니다.")
        result = dict(job)
        if (result.get("status") == "done"
                and "/interior_compositions/" in str(result.get("source_url") or "").replace("\\", "/")
                and not isinstance(result.get("composition_snapshot"), dict)):
            result["quality_review"] = {
                "status": "unverified", "method": "legacy_missing_snapshot",
                "issues": ["submission_snapshot_missing"], "provider_invoked": False,
            }
            result["ready_for_report"] = False
            result.pop("rules_enforced", None)
        # The database stores the job payload separately from its key.  Return
        # the key as part of the public response so clients can persist a
        # stable version identifier after polling completes.
        result.setdefault("job_id", job_id)
        if result.get("status") in {"queued", "pending", "running"}:
            try:
                updated = datetime.fromisoformat(str(result.get("updated_at", "")).replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
            except (ValueError, TypeError):
                age = 0
            threshold = int(result.get("stale_after_s") or JOB_STALE_SECONDS)
            if age > threshold:
                result.update(status="stale", stage="stale", stage_label="작업이 멈춰 있습니다.", retryable=True, error=f"{threshold//60}분 이상 진행 정보가 없습니다. 기존 실행 버튼을 눌러 다시 시도하세요.")
        if result.get("status") == "error":
            result.setdefault("retryable", True)
        return result

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
