"""Keep backend tests isolated from local workspaces and all paid transports."""
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "room-asset-composer"))


@pytest.fixture(autouse=True)
def isolated_backend(monkeypatch, tmp_path):
    from webapp import db, openai_provider, server
    from services import asset_store

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "ASSETS_CSV", tmp_path / "assets" / "assets.csv")
    monkeypatch.setattr(server, "ASSET_OUTPUTS_DIR", tmp_path / "assets" / "outputs")
    monkeypatch.setattr(server, "WEBAPP_TMP", tmp_path / "webapp-tmp")
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "webapp-tmp" / "interior_compositions")
    monkeypatch.setattr(server, "ASSET_EXTRACT_DIR", tmp_path / "webapp-tmp" / "asset_extractions")
    monkeypatch.setattr(server, "INTERIOR_REPORT_DIR", tmp_path / "interior_reports")
    monkeypatch.setattr(server, "PDF_OUTPUT_DIR", tmp_path / "pdf")
    monkeypatch.setattr(server, "FINISH_LIBRARY_DIR", tmp_path / "finish_library")
    monkeypatch.setattr(asset_store, "DATA_DIR", tmp_path / "assets")
    monkeypatch.setattr(asset_store, "UPLOADS_DIR", tmp_path / "assets" / "uploads")
    monkeypatch.setattr(asset_store, "OUTPUTS_DIR", tmp_path / "assets" / "outputs")
    monkeypatch.setattr(asset_store, "ASSETS_CSV", tmp_path / "assets" / "assets.csv")
    monkeypatch.setattr(asset_store, "PROJECT_ROOT", tmp_path / "asset-service")
    monkeypatch.setattr(server, "jobs", {})
    # Tests must neither load a recipient's real key nor make a paid SDK request.
    monkeypatch.setattr(openai_provider, "load_dotenv", lambda *a, **kw: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden_http(*args, **kwargs):
        raise AssertionError("Paid/external HTTP must be explicitly mocked in tests")

    monkeypatch.setattr(httpx, "request", forbidden_http)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_http)
