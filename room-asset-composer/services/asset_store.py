from datetime import datetime
from pathlib import Path
import hashlib
import json
import uuid
import sys

from PIL import Image
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from webapp import db as sqlite_db
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
ASSETS_CSV = DATA_DIR / "assets.csv"

CSV_COLUMNS = [
    "asset_id",
    "project_id",
    "asset_name",
    "category",
    "tags",
    "original_filename",
    "output_filename",
    "method",
    "created_at",
    "output_path",
    "source_path", "mask_path", "thumbnail_path", "provenance",
    "width_mm", "depth_mm", "height_mm", "anchor_point",
    "default_scale", "orientation_deg", "version", "parent_asset_id",
    "content_hash", "archived", "history_json",
]


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "samples").mkdir(parents=True, exist_ok=True)


def make_asset_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{timestamp}_{suffix}"


def _empty_assets_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=CSV_COLUMNS)


def read_assets() -> pd.DataFrame:
    ensure_data_dirs()
    rows = sqlite_db.list_assets()
    if rows:
        frame = pd.DataFrame(rows)
        for column in CSV_COLUMNS:
            if column not in frame.columns: frame[column] = ""
        return frame[CSV_COLUMNS].fillna("")
    if not ASSETS_CSV.exists() or ASSETS_CSV.stat().st_size == 0:
        return _empty_assets_frame()
    try:
        frame = pd.read_csv(ASSETS_CSV, encoding="utf-8-sig", dtype=str)
    except (pd.errors.EmptyDataError, UnicodeDecodeError, OSError):
        return _empty_assets_frame()
    for column in CSV_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[CSV_COLUMNS].fillna("")


def save_asset(
    image: Image.Image,
    asset_name: str,
    category: str,
    tags: str,
    original_filename: str,
    *,
    source_image: Image.Image | None = None,
    mask_image: Image.Image | None = None,
    provenance: str = "original_extraction",
    width_mm: float = 1000,
    depth_mm: float = 500,
    height_mm: float = 1000,
    anchor_point: str = "bottom_center",
    default_scale: float = 1.0,
    orientation_deg: float = 0.0,
    parent_asset_id: str = "",
    project_id: str = "",
) -> dict:
    ensure_data_dirs()

    asset_id = make_asset_id()
    output_filename = f"{asset_id}.png"
    output_path = OUTPUTS_DIR / output_filename
    image.convert("RGBA").save(output_path, format="PNG")
    content_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    current = read_assets()
    same_project = current["project_id"].fillna("").astype(str) == str(project_id or "")
    duplicate = current[(current["content_hash"] == content_hash) & same_project & (current["archived"] != "true")]
    if not duplicate.empty:
        output_path.unlink(missing_ok=True)
        return duplicate.iloc[-1].to_dict()
    family = current[(current["asset_id"] == parent_asset_id) | (current["parent_asset_id"] == parent_asset_id)] if parent_asset_id else current.iloc[0:0]
    version = 1 + max([int(float(value or 0)) for value in family["version"].tolist()] or [0])

    source_path = UPLOADS_DIR / f"{asset_id}_source.png"
    (source_image or image).convert("RGBA").save(source_path, format="PNG")
    mask_path = UPLOADS_DIR / f"{asset_id}_mask.png"
    if mask_image is not None:
        mask_image.convert("L").save(mask_path, format="PNG")
    thumbnail_path = OUTPUTS_DIR / f"{asset_id}_thumb.png"
    thumbnail = image.convert("RGBA").copy(); thumbnail.thumbnail((320, 320)); thumbnail.save(thumbnail_path, format="PNG")

    relative_output_path = Path("data") / "outputs" / output_filename
    row = {
        "asset_id": asset_id,
        "project_id": project_id,
        "asset_name": asset_name.strip() or "이름 없음",
        "category": category,
        "tags": tags.strip(),
        "original_filename": original_filename,
        "output_filename": output_filename,
        "method": "rembg",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_path": str(relative_output_path),
        "source_path": str(Path("data/uploads") / source_path.name),
        "mask_path": str(Path("data/uploads") / mask_path.name) if mask_image is not None else "",
        "thumbnail_path": str(Path("data/outputs") / thumbnail_path.name),
        "provenance": provenance,
        "width_mm": str(width_mm), "depth_mm": str(depth_mm), "height_mm": str(height_mm),
        "anchor_point": anchor_point, "default_scale": str(default_scale),
        "orientation_deg": str(orientation_deg), "version": str(version),
        "parent_asset_id": parent_asset_id, "content_hash": content_hash,
        "archived": "false", "history_json": "[]",
    }

    updated = pd.concat([current, pd.DataFrame([row])], ignore_index=True)
    updated.to_csv(ASSETS_CSV, index=False, encoding="utf-8-sig")
    sqlite_db.upsert_asset(row)
    return row


def resolve_asset_path(output_path: str) -> Path:
    path = Path(str(output_path))
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def list_recent_assets(limit: int = 12, project_id: str = "") -> pd.DataFrame:
    frame = read_assets()
    frame = frame[frame["archived"] != "true"]
    if project_id:
        project_values = frame["project_id"].fillna("").astype(str)
        frame = frame[(project_values == "") | (project_values == project_id)]
    if frame.empty:
        return frame
    sorted_frame = frame.sort_values("created_at", ascending=False)
    return sorted_frame.head(limit).reset_index(drop=True)


def archive_asset(asset_id: str) -> bool:
    frame = read_assets(); match = frame["asset_id"] == asset_id
    if not match.any(): return False
    frame.loc[match, "archived"] = "true"
    frame.to_csv(ASSETS_CSV, index=False, encoding="utf-8-sig"); sqlite_db.replace_assets(frame.to_dict("records")); return True


def add_history(asset_id: str, event: dict) -> bool:
    frame = read_assets(); indexes = frame.index[frame["asset_id"] == asset_id].tolist()
    if not indexes: return False
    index = indexes[-1]
    try: history = json.loads(frame.at[index, "history_json"] or "[]")
    except json.JSONDecodeError: history = []
    history.append({"at": datetime.now().isoformat(timespec="seconds"), **event})
    frame.at[index, "history_json"] = json.dumps(history, ensure_ascii=False)
    frame.to_csv(ASSETS_CSV, index=False, encoding="utf-8-sig"); sqlite_db.replace_assets(frame.to_dict("records")); return True
