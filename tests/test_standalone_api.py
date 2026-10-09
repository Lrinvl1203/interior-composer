"""Standalone boundaries, empty installation and persisted-job regression tests."""
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from webapp import db, modelark, openai_provider, server


def test_empty_install_returns_zero_assets_and_optional_empty_catalog():
    client = TestClient(server.app)
    assert client.get("/api/assets").json() == []
    assert client.get("/api/finish-library").json() == {"categories": [], "items": []}
    assert client.get("/api/health").json() == {"status": "ok", "service": "interior-composer"}


def test_database_has_only_composer_tables():
    db.init_db()
    with db.connect() as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"assets", "jobs"}


def test_configuration_is_rooted_at_standalone_tree():
    assert server.BASE == Path(__file__).resolve().parents[1]
    assert modelark.DOTENV_PATH == server.BASE / ".env"
    assert openai_provider.DOTENV_PATH == server.BASE / ".env"
    assert not hasattr(server, "CODEX_CLI")
    assert not hasattr(server, "BLENDER")
    assert not hasattr(server, "SPIKE")


@pytest.mark.parametrize("path", ["/api/projects", "/api/saas/health", "/api/auto-pipeline", "/api/camera-director/views", "/api/bake"])
def test_unrelated_product_apis_are_absent(path):
    assert TestClient(server.app).get(path).status_code == 404


@pytest.mark.parametrize("mode,key", [("flash", "OPENAI_API_KEY"), ("premium", "OPENAI_API_KEY")])
def test_missing_provider_fails_without_any_cli_or_network_fallback(monkeypatch, tmp_path, mode, key):
    monkeypatch.setattr(server, "openai_is_configured", lambda: False)
    called = []
    monkeypatch.setattr(server, "openai_generate_image", lambda *a, **kw: called.append("premium"))
    output = tmp_path / "result.png"
    with pytest.raises(ValueError, match=key):
        server.generate_ai_render("request", [], output, "test", [], ai_mode=mode)
    assert called == []
    assert not output.exists()


def test_completed_job_recovers_without_replaying_provider_or_worker(monkeypatch):
    job_id = "a" * 32
    stored = {"status": "done", "result_url": "/webapp/tmp/interior_compositions/aaaaaaaa/after.png",
              "result_revision": 2, "result_sha256": "b" * 64, "frame_aligned": True,
              "ready_for_report": True, "composition_snapshot": {"assets": [], "finishes": []},
              "quality_review": {"status": "passed"}, "provider_calls": [{"model": "test", "usage": {"total_tokens": 8}}]}
    db.put_job(job_id, stored)
    server.jobs.clear()
    monkeypatch.setattr(server, "start_job_worker", lambda *a, **kw: pytest.fail("Reading a saved job must never restart paid work"))
    result = TestClient(server.app).get(f"/api/jobs/{job_id}").json()
    assert result == {**stored, "job_id": job_id}


def test_interrupted_old_job_recovers_as_retryable_stale_without_generation():
    job_id = "c" * 32
    db.put_job(job_id, {"status": "running", "updated_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "stale_after_s": 600})
    server.jobs.clear()
    result = TestClient(server.app).get(f"/api/jobs/{job_id}").json()
    assert result["status"] == "stale"
    assert result["retryable"] is True


def test_expected_image_routes_work_without_serving_metadata(tmp_path):
    root = server.WEBAPP_TMP
    result_dir = root / "interior_compositions" / "12345678"
    result_dir.mkdir(parents=True)
    image = result_dir / "after.png"
    Image.new("RGB", (16, 16), "orange").save(image)
    (result_dir / "prompt.txt").write_text("private prompt", encoding="utf-8")
    (result_dir / "job.json").write_text('{"private":"metadata"}', encoding="utf-8")
    client = TestClient(server.app)
    result = client.get("/webapp/tmp/interior_compositions/12345678/after.png")
    assert result.status_code == 200
    assert result.headers["content-type"] == "image/png"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert client.get("/webapp/tmp/interior_compositions/12345678/prompt.txt").status_code == 404
    assert client.get("/webapp/tmp/interior_compositions/12345678/job.json").status_code == 404


@pytest.mark.parametrize("path", [
    "/.env", "/webapp/.env", "/webapp/data/interior.db", "/webapp/logs/server.log",
    "/webapp/data/finish_library/catalog.json", "/room-asset-composer/data/assets.csv",
    "/webapp/data/interior_reports/" + "a" * 32 + "/report.json",
    "/webapp/tmp/%2e%2e/data/interior.db", "/webapp/tmp/..%5c.env",
])
def test_sensitive_and_traversal_paths_are_not_public(path):
    assert TestClient(server.app).get(path).status_code == 404


def test_image_handler_rejects_absolute_and_parent_paths(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    secret = tmp_path / "private.png"
    Image.new("RGB", (8, 8), "black").save(secret)
    for reference in (str(secret), "../private.png"):
        with pytest.raises(HTTPException) as failure:
            server.image_file_response(root, reference)
        assert failure.value.status_code == 404


def test_image_handler_rejects_symlinks_escaping_static_root(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    secret = tmp_path / "private.png"
    Image.new("RGB", (8, 8), "black").save(secret)
    try:
        (root / "visible.png").symlink_to(secret)
    except OSError:
        pytest.skip("OS does not permit test symlink creation")
    with pytest.raises(HTTPException) as failure:
        server.image_file_response(root, "visible.png")
    assert failure.value.status_code == 404


def test_public_url_conversion_rejects_nonresult_files(tmp_path):
    with pytest.raises(ValueError):
        server.path_to_url(tmp_path / ".env")


def test_large_upload_rejected_before_image_decode():
    from starlette.datastructures import UploadFile
    file = UploadFile(io.BytesIO(b"x" * (25 * 1024 * 1024 + 1)), filename="oversized.png")
    with pytest.raises(ValueError):
        server.load_uploaded_image(file)


def test_sam_services_are_not_imported_for_normal_api_start():
    import sys
    assert "services.sam_client" not in sys.modules


@pytest.mark.parametrize("origin", [
    "https://attacker.invalid", "http://localhost.evil:3100", "null", "http://localhost:6666",
    "http://localhost:3100@attacker.invalid", "http://attacker.invalid@localhost:3100",
    "http://localhost:3100/path", "http://localhost:3100?forged=yes", "https://localhost:3100",
])
def test_hostile_or_nonapproved_mutation_origin_is_blocked(origin):
    result = TestClient(server.app).post("/api/interior-reports", headers={"Origin": origin}, json={})
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "ORIGIN_FORBIDDEN"


@pytest.mark.parametrize("origin", ["http://localhost:3100", "http://127.0.0.1:3100", "http://localhost:18877", "http://127.0.0.1:18877"])
def test_known_local_origin_reaches_handler(origin):
    result = TestClient(server.app).post("/api/interior-reports", headers={"Origin": origin}, json={})
    assert result.status_code == 400  # Invalid report input, not an origin rejection.


def test_configured_ports_and_direct_loopback_api_origin(monkeypatch):
    monkeypatch.setenv("INTERIOR_FRONTEND_PORT", "3200")
    assert TestClient(server.app).post("/api/interior-reports", headers={"Origin": "http://localhost:3200"}, json={}).status_code == 400
    assert TestClient(server.app).post("/api/interior-reports", headers={"Origin": "http://localhost:3100"}, json={}).status_code == 403
    direct = TestClient(server.app, base_url="http://127.0.0.1:19999")
    assert direct.post("/api/interior-reports", headers={"Origin": "http://127.0.0.1:19999"}, json={}).status_code == 400


def test_cross_site_fetch_is_blocked_even_without_origin():
    client = TestClient(server.app)
    assert client.post("/api/interior-reports", headers={"Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403
    assert client.post("/api/interior-reports", json={}).status_code == 400


def test_nonloopback_request_host_cannot_whitelist_its_own_origin():
    client = TestClient(server.app, base_url="http://attacker.invalid:3100")
    assert client.post("/api/interior-reports", headers={"Origin": "http://attacker.invalid:3100"}, json={}).status_code == 403


@pytest.mark.parametrize("mode,key", [("flash", "OPENAI_API_KEY"), ("premium", "OPENAI_API_KEY")])
def test_missing_key_worker_error_is_available_to_browser_polling(monkeypatch, tmp_path, mode, key):
    monkeypatch.setattr(server, "openai_is_configured", lambda: False)
    source = tmp_path / "before.png"
    Image.new("RGB", (32, 24), "white").save(source)
    job_id = server.create_job()
    server.interior_compose_worker(job_id, source, [], [], {}, [], "", mode)
    response = TestClient(server.app).get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "error"
    assert job["stage"] == "error"
    assert job["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert key in job["error"]
    assert ".env" in job["error"]
    assert job["retryable"] is True
    assert job["ready_for_report"] is False
    assert str(tmp_path) not in job["error"]
    assert db.get_job(job_id)["error"] == job["error"]


def test_arbitrary_provider_errors_and_logs_are_not_exposed_to_browser():
    job_id = server.create_job()
    secret = "provider-secret-token"
    path = r"C:\private\credentials.env"
    server.finish_job_error(job_id, RuntimeError(f"{secret}: raw body from {path}"), [], f"raw request {secret} {path}")
    job = TestClient(server.app).get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "error"
    assert job["error_code"] == "GENERATION_FAILED"
    assert "RuntimeError" in job["error"]
    assert secret not in str(job)
    assert path not in str(job)
    assert secret not in str(db.get_job(job_id))
