"""Target-local SQLite persistence for Composer assets and jobs only.

SQL/record contracts retained from source commit 4d8ef239; unrelated project,
subscription, billing, referral and analytics tables are intentionally absent.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent / "data" / "interior.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS assets(
            asset_id TEXT PRIMARY KEY, asset_name TEXT NOT NULL, category TEXT, tags TEXT,
            original_filename TEXT, output_filename TEXT, method TEXT, created_at TEXT NOT NULL,
            output_path TEXT, data_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
            job_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'generic', status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}', error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'local-user');
        CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC);
        """)


def _asset_values(row: dict[str, Any]) -> tuple:
    return (row["asset_id"], row.get("asset_name", ""), row.get("category", ""), row.get("tags", ""),
            row.get("original_filename", ""), row.get("output_filename", ""), row.get("method", ""),
            row.get("created_at") or now(), row.get("output_path", ""), json.dumps(row, ensure_ascii=False))


def upsert_asset(row: dict[str, Any], path: Path | None = None) -> None:
    init_db(path)
    with connect(path) as connection:
        connection.execute("""INSERT INTO assets(asset_id,asset_name,category,tags,original_filename,output_filename,method,created_at,output_path,data_json)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET asset_name=excluded.asset_name,
            category=excluded.category,tags=excluded.tags,original_filename=excluded.original_filename,
            output_filename=excluded.output_filename,method=excluded.method,created_at=excluded.created_at,
            output_path=excluded.output_path,data_json=excluded.data_json""", _asset_values(row))


def replace_assets(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    init_db(path)
    with connect(path) as connection:
        connection.execute("DELETE FROM assets")
        connection.executemany("INSERT INTO assets(asset_id,asset_name,category,tags,original_filename,output_filename,method,created_at,output_path,data_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               [_asset_values(row) for row in rows])


def list_assets(path: Path | None = None) -> list[dict[str, Any]]:
    init_db(path)
    with connect(path) as connection:
        return [json.loads(row["data_json"]) for row in connection.execute("SELECT data_json FROM assets ORDER BY created_at DESC")]


def put_job(job_id: str, data: dict[str, Any], kind: str = "generic", path: Path | None = None) -> None:
    init_db(path)
    stamp = now()
    with connect(path) as connection:
        connection.execute("""INSERT INTO jobs(job_id,kind,status,payload_json,result_json,error,created_at,updated_at,user_id)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
            result_json=excluded.result_json,error=excluded.error,updated_at=excluded.updated_at""",
            (job_id, kind, str(data.get("status", "queued")), "{}", json.dumps(data, ensure_ascii=False),
             str(data.get("error") or "") or None, stamp, stamp, data.get("user_id", "local-user")))


def get_job(job_id: str, path: Path | None = None) -> dict[str, Any] | None:
    init_db(path)
    with connect(path) as connection:
        row = connection.execute("SELECT result_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return None if row is None else json.loads(row["result_json"])
