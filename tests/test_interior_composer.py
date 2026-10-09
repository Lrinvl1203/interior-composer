import io
import json
import pytest

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from webapp import server
from webapp.image_align import lock_composite_to_source
from webapp.interior_report import korean_report_date
from services import ai_asset


def png_bytes(color=(190, 160, 120, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (96, 64), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_interior_compose_prompt_locks_inventory_and_camera():
    prompt = server.build_interior_compose_prompt(
        assets=[
            {"asset_id": "chair-1", "asset_name": "Oak chair"},
            {"asset_id": "table-1", "asset_name": "Round table"},
        ],
        finishes=[{"id": "wp-1", "category": "wallpaper", "name": "Warm linen"}],
        finish_targets={"wallpaper": "accent_wall"},
        placements=[{"asset_id": "chair-1", "x": 0.2, "y": 0.4, "width": 0.2, "height": 0.3}],
        user_request="Keep generous negative space.",
    )

    assert "Use every listed asset exactly once" in prompt
    assert "Do not duplicate, omit" in prompt
    assert "Preserve the exact canvas dimensions" in prompt
    assert "user-specified normalized frame box" in prompt
    assert "automatic optimal placement" in prompt
    assert "accent_wall" in prompt
    assert "Keep generous negative space." in prompt


def test_finish_only_prompt_preserves_existing_furniture():
    prompt = server.build_interior_compose_prompt(
        assets=[],
        finishes=[{"id": "wp-1", "category": "wallpaper", "name": "Warm linen"}],
        finish_targets={"wallpaper": "all_walls"},
        placements=[],
        user_request="",
    )

    assert "FINISH-ONLY EDIT" in prompt
    assert "Preserve every existing piece of furniture" in prompt
    assert "No furniture asset is selected" in prompt
    assert "Do not remove, replace, rearrange" in prompt
    assert "Remove existing loose furniture" not in prompt


def test_interior_compose_endpoint_queues_finish_only_edit(tmp_path, monkeypatch):
    finish_root = tmp_path / "finishes"
    finish_path = finish_root / "wallpaper" / "wp-1.png"
    finish_path.parent.mkdir(parents=True)
    finish_path.write_bytes(png_bytes())
    catalog = {
        "categories": [{"key": "wallpaper", "label": "벽지"}],
        "items": [{"id": "wp-1", "category": "wallpaper", "name": "Warm linen"}],
    }
    monkeypatch.setattr(server, "FINISH_LIBRARY_DIR", finish_root)
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "compose")
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "finish-only.db")
    monkeypatch.setattr(server, "path_to_url", lambda path: f"/test/{path.name}")
    monkeypatch.setattr(server, "finish_library_data", lambda: catalog)
    monkeypatch.setattr(server, "read_asset_rows", lambda: [])
    captured = {}

    def capture_worker(category, job_id, target, *args):
        captured.update(category=category, job_id=job_id, target=target, args=args)

    monkeypatch.setattr(server, "start_job_worker", capture_worker)
    client = TestClient(server.app)
    response = client.post(
        "/api/interior-compose",
        files={"before": ("before.png", png_bytes(), "image/png")},
        data={
            "asset_ids": "[]",
            "finishes": json.dumps({"wallpaper": "wp-1"}),
            "finish_targets": json.dumps({"wallpaper": "all_walls"}),
            "placements": "[]",
        },
    )

    assert response.status_code == 200
    assert response.json()["asset_count"] == 0
    assert response.json()["finish_count"] == 1
    assert captured["args"][1] == []
    assert [item["id"] for item in captured["args"][2]] == ["wp-1"]


def test_interior_compose_endpoint_rejects_empty_selection():
    client = TestClient(server.app)
    response = client.post(
        "/api/interior-compose",
        files={"before": ("before.png", png_bytes(), "image/png")},
        data={"asset_ids": "[]", "finishes": "{}"},
    )

    assert response.status_code == 400
    assert "에셋 또는 마감재" in response.json()["detail"]


def test_interior_compose_endpoint_rejects_duplicate_assets(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "chair.png").write_bytes(png_bytes())
    monkeypatch.setattr(server, "ASSET_OUTPUTS_DIR", outputs)
    monkeypatch.setattr(server, "read_asset_rows", lambda: [{
        "asset_id": "chair-1",
        "asset_name": "Chair",
        "category": "furniture",
        "output_filename": "chair.png",
        "archived": "false",
    }])
    client = TestClient(server.app)

    response = client.post(
        "/api/interior-compose",
        files={"before": ("before.png", png_bytes(), "image/png")},
        data={"asset_ids": json.dumps(["chair-1", "chair-1"])},
    )

    assert response.status_code == 400
    assert "한 번만" in response.json()["detail"]


def test_interior_compose_endpoint_queues_selected_assets_once(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "chair.png").write_bytes(png_bytes())
    compose_dir = tmp_path / "compose"
    monkeypatch.setattr(server, "ASSET_OUTPUTS_DIR", outputs)
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", compose_dir)
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "interior.db")
    monkeypatch.setattr(server, "path_to_url", lambda path: f"/test/{path.name}")
    monkeypatch.setattr(server, "read_asset_rows", lambda: [{
        "asset_id": "chair-1",
        "asset_name": "Chair",
        "category": "furniture",
        "output_filename": "chair.png",
        "archived": "false",
    }])
    captured = {}

    def capture_worker(category, job_id, target, *args):
        captured.update(category=category, job_id=job_id, target=target, args=args)

    monkeypatch.setattr(server, "start_job_worker", capture_worker)
    client = TestClient(server.app)
    response = client.post(
        "/api/interior-compose",
        files={"before": ("before.png", png_bytes(), "image/png")},
        data={
            "asset_ids": json.dumps(["chair-1"]),
            "finishes": "{}",
            "finish_targets": "{}",
            "placements": json.dumps([{"asset_id": "chair-1", "x": 0.2, "y": 0.3, "width": 0.25, "height": 0.3}]),
            "ai_mode": "premium",
        },
    )

    assert response.status_code == 200
    assert response.json()["asset_count"] == 1
    assert response.json()["placement_count"] == 1
    snapshot = server.get_job(response.json()["job_id"])["composition_snapshot"]
    assert snapshot["assets"][0]["asset_id"] == "chair-1"
    assert snapshot["assets"][0]["sha256"] == server.file_sha256(outputs / "chair.png")
    assert snapshot["placements"][0]["asset_id"] == "chair-1"
    assert snapshot["ai_mode"] == "premium"
    assert captured["category"] == "image"
    assert captured["target"] is server.interior_compose_worker
    queued_assets = captured["args"][1]
    assert [item["asset_id"] for item in queued_assets] == ["chair-1"]


def test_pixel_lock_keeps_before_coordinates_and_architecture(tmp_path):
    source_path = tmp_path / "before.png"
    result_path = tmp_path / "generated.png"

    source = Image.new("RGB", (320, 220), (224, 218, 205))
    source_draw = ImageDraw.Draw(source)
    source_draw.line((18, 38, 302, 38), fill=(35, 35, 35), width=4)
    source_draw.line((24, 38, 24, 205), fill=(35, 35, 35), width=4)
    source_draw.line((18, 177, 302, 177), fill=(70, 70, 70), width=3)
    source_draw.rectangle((62, 132, 117, 176), fill=(145, 75, 45))
    source.save(source_path)

    generated = source.copy()
    generated_draw = ImageDraw.Draw(generated)
    generated_draw.rectangle((58, 128, 121, 176), fill=(224, 218, 205))
    generated_draw.rectangle((190, 116, 260, 176), fill=(35, 88, 155))
    generated.save(result_path)

    report = lock_composite_to_source(source_path, result_path)
    output = Image.open(result_path).convert("RGB")

    assert output.size == source.size
    assert report["pixel_coordinate_locked"] is True
    assert 0 < report["unchanged_pixel_ratio"] < 1
    assert output.getpixel((120, 38)) == source.getpixel((120, 38))
    assert output.getpixel((24, 100)) == source.getpixel((24, 100))
    assert output.getpixel((220, 145)) != source.getpixel((220, 145))


@pytest.mark.parametrize("line_shape", ["opencv4", "opencv5"])
def test_pixel_lock_accepts_hough_line_shapes(tmp_path, monkeypatch, line_shape):
    import cv2
    import numpy as np

    source_path, result_path = tmp_path / "before.png", tmp_path / "after.png"
    source = Image.new("RGB", (320, 220), "white")
    ImageDraw.Draw(source).line((10, 30, 310, 30), fill="black", width=4)
    source.save(source_path)
    result = source.copy()
    ImageDraw.Draw(result).rectangle((130, 100, 200, 180), fill="blue")
    result.save(result_path)
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: np.array([[[10, 30, 310, 30]]], dtype=np.int32).reshape((-1, 4) if line_shape == "opencv5" else (-1, 1, 4)))
    assert lock_composite_to_source(source_path, result_path)["pixel_coordinate_locked"] is True


def test_global_relighting_uses_clean_registered_frame_without_ghost_edges(tmp_path, monkeypatch):
    import numpy as np
    from webapp import image_align

    source_path, result_path = tmp_path / "before.png", tmp_path / "after.png"
    source = Image.new("RGB", (320, 220), (95, 90, 80))
    draw = ImageDraw.Draw(source)
    draw.line((20, 40, 300, 40), fill=(10, 10, 10), width=5)
    draw.rectangle((125, 55, 195, 140), outline=(15, 15, 15), width=5)
    source.save(source_path)
    generated = Image.new("RGB", source.size, (155, 145, 130))
    draw = ImageDraw.Draw(generated)
    draw.line((20, 42, 300, 42), fill=(20, 20, 20), width=5)
    draw.rectangle((125, 57, 195, 142), outline=(20, 20, 20), width=5)
    draw.ellipse((80, 145, 235, 205), fill=(240, 240, 235))
    generated.save(result_path)
    monkeypatch.setattr(image_align, "_register_to_source", lambda before, after: (after, True, .7154))
    monkeypatch.setattr(image_align, "_shared_structure_mask", lambda *args: (_ for _ in ()).throw(AssertionError("old edges must not be pasted")))
    report = lock_composite_to_source(source_path, result_path)
    output = np.asarray(Image.open(result_path).convert("RGB"))
    assert np.array_equal(output, np.asarray(generated))
    assert report["alignment_method"] == "affine_registration_full_frame"
    assert report["global_change_fraction"] > .35
    assert report["architecture_validated"] is False


def test_ai_asset_extraction_requires_text_description():
    client = TestClient(server.app)
    response = client.post(
        "/api/extract",
        files={"file": ("room.png", png_bytes(), "image/png")},
        data={
            "mode": "ai_box",
            "prompt": json.dumps({"box": [5, 5, 80, 55]}),
            "description": "",
        },
    )

    assert response.status_code == 400
    assert "텍스트로 설명" in response.json()["detail"]


def test_ai_asset_extraction_queues_box_and_description(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(server, "ASSET_EXTRACT_DIR", tmp_path / "extract")
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")

    def capture_worker(category, job_id, target, *args):
        captured.update(category=category, job_id=job_id, target=target, args=args)

    monkeypatch.setattr(server, "start_job_worker", capture_worker)
    client = TestClient(server.app)
    response = client.post(
        "/api/extract",
        files={"file": ("room.png", png_bytes(), "image/png")},
        data={
            "mode": "ai_box",
            "prompt": json.dumps({"box": [5, 6, 80, 56]}),
            "description": "월넛 프레임의 베이지 패브릭 암체어",
            "ai_mode": "premium",
            "asset_name": "암체어 01",
            "save": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == captured["job_id"]
    assert captured["category"] == "image"
    assert captured["target"] is server.ai_asset_extract_worker
    assert captured["args"][1] == (5, 6, 80, 56)
    assert captured["args"][2] == "월넛 프레임의 베이지 패브릭 암체어"
    assert captured["args"][3] == "premium"
    assert captured["args"][4] is True
    assert captured["args"][5] == "암체어 01"


def test_ai_asset_extract_worker_saves_job_result(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    source_path = tmp_path / "source.png"
    Image.new("RGBA", (96, 64), (190, 160, 120, 255)).save(source_path)
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "worker.db")
    monkeypatch.setattr(ai_asset, "extract_selected_asset", lambda image, *args, **kwargs: image)
    monkeypatch.setattr(server, "save_asset_with_method", lambda *args, **kwargs: {
        "asset_id": "asset-ai-1",
        "output_filename": "asset-ai-1.png",
    })
    job_id = server.create_job()

    server.ai_asset_extract_worker(
        job_id,
        source_path,
        (5, 6, 80, 56),
        "베이지 암체어",
        "premium",
        True,
        "암체어 01",
        "furniture",
        "interior-composer",
        "room.png",
    )

    job = server.get_job(job_id)
    assert job["status"] == "done"
    assert job["asset_id"] == "asset-ai-1"
    assert job["result_url"].endswith("/asset-ai-1.png")
    assert job["provider"] == "openai"
    assert job["provider_model"] == ai_asset.MODEL
    assert job["provider_usage"] == {}
    assert job["provider_usage_available"] is False


def test_ai_asset_worker_prefers_actual_provider_metadata_before_postprocessing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    source_path = tmp_path / "source.png"
    Image.new("RGBA", (96, 64), (190, 160, 120, 255)).save(source_path)
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "worker-metadata.db")
    extracted = Image.new("RGBA", (96, 64), (190, 160, 120, 255))
    extracted.info["provider_metadata"] = {
        "provider": "openai", "model": "actual-pro-model",
        "usage": {"total_tokens": 17, "secret_note": "discard this"},
        "usage_available": True,
    }
    monkeypatch.setattr(ai_asset, "extract_selected_asset", lambda image, *args, **kwargs: extracted)
    monkeypatch.setattr(server, "save_asset_with_method", lambda *args, **kwargs: {
        "asset_id": "asset-ai-2", "output_filename": "asset-ai-2.png",
    })
    job_id = server.create_job()
    server.ai_asset_extract_worker(job_id, source_path, (5, 6, 80, 56), "A chair", "flash", True,
                                   "Chair", "furniture", "interior-composer", "room.png")
    job = server.get_job(job_id)
    assert job["status"] == "done"
    assert job["provider"] == "openai"
    assert job["provider_model"] == "actual-pro-model"
    assert job["provider_usage"] == {"total_tokens": 17}
    assert job["provider_usage_available"] is True


def test_interior_report_persists_pdf_share_link_and_versions(tmp_path, monkeypatch):
    compose_dir = tmp_path / "compose"
    report_dir = tmp_path / "reports"
    pdf_dir = tmp_path / "pdf"
    asset_dir = tmp_path / "assets"
    finish_dir = tmp_path / "finishes"
    asset_dir.mkdir()
    (asset_dir / "chair.png").write_bytes(png_bytes((150, 95, 55, 255)))
    finish_image = finish_dir / "wallpaper" / "wp-1.png"
    finish_image.parent.mkdir(parents=True)
    finish_image.write_bytes(png_bytes((218, 205, 180, 255)))

    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", compose_dir)
    monkeypatch.setattr(server, "INTERIOR_REPORT_DIR", report_dir)
    monkeypatch.setattr(server, "PDF_OUTPUT_DIR", pdf_dir)
    monkeypatch.setattr(server, "ASSET_OUTPUTS_DIR", asset_dir)
    monkeypatch.setattr(server, "FINISH_LIBRARY_DIR", finish_dir)
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "reports.db")
    monkeypatch.setattr(server, "read_asset_rows", lambda: [{
        "asset_id": "chair-1",
        "asset_name": "Oak chair",
        "category": "furniture",
        "output_filename": "chair.png",
        "archived": "false",
    }])
    monkeypatch.setattr(server, "finish_library_data", lambda: {
        "categories": [{"key": "wallpaper", "label": "벽지"}],
        "items": [{"id": "wp-1", "category": "wallpaper", "name": "Warm linen"}],
    })

    job_id = server.create_job()
    job_root = compose_dir / job_id[:8]
    job_root.mkdir(parents=True)
    before_path, after_path = job_root / "before.png", job_root / "after.png"
    before_path.write_bytes(png_bytes((220, 215, 205, 255)))
    after_path.write_bytes(png_bytes((205, 195, 180, 255)))
    snapshot_asset, snapshot_finish = job_root / "input-asset-1.png", job_root / "input-finish-1.png"
    snapshot_asset.write_bytes((asset_dir / "chair.png").read_bytes())
    snapshot_finish.write_bytes(finish_image.read_bytes())
    server.update_job(
        job_id,
        status="done",
        asset_ids=["chair-1"],
        finish_ids=["wp-1"],
        ai_mode="premium",
        frame_aligned=True,
        quality_review={"status": "passed", "method": "mock"},
        ready_for_report=True,
        result_sha256=server.file_sha256(after_path),
        composition_snapshot={
            "assets": [{"asset_id": "chair-1", "name": "Oak chair", "category": "furniture", "source_path": str(snapshot_asset), "sha256": server.file_sha256(snapshot_asset)}],
            "finishes": [{"id": "wp-1", "name": "Warm linen", "category": "wallpaper", "target": "accent_wall", "source_path": str(snapshot_finish), "sha256": server.file_sha256(snapshot_finish)}],
            "placements": [{"asset_id": "chair-1", "x": .2, "y": .2, "width": .2, "height": .2}],
            "finish_targets": {"wallpaper": "accent_wall"},
            "user_request": "Keep the window clear.", "ai_mode": "premium",
            "source_sha256": server.file_sha256(before_path),
        },
    )

    client = TestClient(server.app)
    first_payload = {
        "job_id": job_id,
        "title": "Living room consultation",
        "space_type": "거실",
        "request": "Ignore this edited request",
        "placements": [],
        "finish_targets": {"wallpaper": "all_walls"},
    }
    server.update_job(job_id, ready_for_report=False)
    assert client.post("/api/interior-reports", json=first_payload).status_code == 409
    server.update_job(job_id, ready_for_report=True)
    first = client.post("/api/interior-reports", json=first_payload)

    assert first.status_code == 200
    first_report = first.json()
    assert first_report["version"] == 1
    assert first_report["assets"][0]["placement"] == "manual"
    assert first_report["finishes"][0]["target"] == "accent_wall"
    assert first_report["request"] == "Keep the window clear."
    assert first_report["created_at"].endswith("+09:00")
    assert first_report["share_url"].endswith(first_report["report_id"])
    assert len(first_report["versions"]) == 1
    pdf_path = pdf_dir / f"interior-report-{first_report['report_id']}.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert pdf_path.stat().st_size > 5000

    retry = client.post("/api/interior-reports", json=first_payload)
    assert retry.status_code == 200
    assert retry.json()["report_id"] == first_report["report_id"]
    assert len(retry.json()["versions"]) == 1

    second = client.post("/api/interior-reports", json={
        "job_id": job_id,
        "title": "Living room consultation",
        "space_type": "거실",
        "placements": [],
        "finish_targets": {"wallpaper": "all_walls"},
        "previous_report_id": first_report["report_id"],
    })

    assert second.status_code == 200
    second_report = second.json()
    assert second_report["version"] == 2
    assert second_report["assets"][0]["placement"] == "manual"
    assert second_report["finishes"][0]["target"] == "accent_wall"
    assert second_report["series_id"] == first_report["series_id"]
    assert [item["version"] for item in second_report["versions"]] == [1, 2]
    downloaded = client.get(second_report["pdf_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"


def test_legacy_report_is_unverifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    job_id = server.create_job()
    server.update_job(job_id, status="done", ready_for_report=True,
                      source_url=f"/webapp/tmp/interior_compositions/{job_id[:8]}/before.png",
                      rules_enforced={"single_use": True})
    legacy = server.get_job(job_id)
    assert legacy["quality_review"]["status"] == "unverified"
    assert legacy["ready_for_report"] is False
    assert "rules_enforced" not in legacy
    response = TestClient(server.app).post("/api/interior-reports", json={"job_id": job_id})
    assert response.status_code == 409
    assert "입력 기록" in response.json()["detail"]


def test_manual_review_requires_current_identity_and_unverified_status(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "compose")
    job_id = server.create_job()
    root = tmp_path / "compose" / job_id[:8]
    root.mkdir(parents=True)
    after = root / "after.png"
    after.write_bytes(png_bytes())
    digest = server.file_sha256(after)
    server.update_job(job_id, status="done", composition_snapshot={"assets": []}, frame_aligned=True,
                      quality_review={"status": "unverified"}, result_revision=2, result_sha256=digest,
                      ready_for_report=False)
    client = TestClient(server.app)
    base = {"result_revision": 2, "result_sha256": digest, "inventory": True, "structure": True, "finishes": True}
    assert client.post(f"/api/interior-compose/{job_id}/review", json={**base, "result_revision": 1}).status_code == 409
    assert client.post(f"/api/interior-compose/{job_id}/review", json={**base, "inventory": False}).status_code == 400
    accepted = client.post(f"/api/interior-compose/{job_id}/review", json=base)
    assert accepted.status_code == 200
    assert accepted.json()["ready_for_report"] is True
    assert accepted.json()["manual_visual_review"]["result_sha256"] == digest
    after.write_bytes(png_bytes((1, 2, 3, 255)))
    assert client.post(f"/api/interior-compose/{job_id}/review", json=base).status_code == 409
    after.write_bytes(png_bytes())
    server.update_job(job_id, quality_review={"status": "failed"}, ready_for_report=False)
    assert client.post(f"/api/interior-compose/{job_id}/review", json=base).status_code == 409


def test_generate_ai_render_persists_provider_usage_without_price(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(server, "openai_is_configured", lambda: True)
    monkeypatch.setattr(server, "openai_generate_image", lambda *args, **kwargs: {"model": "test-image", "usage": {"input_tokens": 10, "debug_secret": "never persist"}})
    job_id = server.create_job()
    server.generate_ai_render("prompt", [], tmp_path / "output.png", "test", [], job_id=job_id, ai_mode="premium")
    job = server.get_job(job_id)
    assert job["provider"] == "openai"
    assert job["provider_model"] == "test-image"
    assert job["provider_usage"] == {"input_tokens": 10}
    assert "billed_amount" not in job


def test_pdf_date_converts_utc_to_korea_calendar_day():
    assert korean_report_date("2026-10-08T15:30:00+00:00") == "2026-10-09"
    assert korean_report_date("2026-10-09T00:30:00+09:00") == "2026-10-09"


def test_composer_repairs_once_and_preserves_both_raw_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "compose")
    monkeypatch.setattr(server, "path_to_url", lambda path: f"/test/{path.name}")
    job_id = server.create_job()
    root = tmp_path / "compose" / job_id[:8]
    root.mkdir(parents=True)
    source = root / "before.png"
    source.write_bytes(png_bytes())
    server.update_job(job_id, composition_snapshot={"assets": [], "finishes": [], "placements": [], "source_sha256": server.file_sha256(source)})
    prompts = []
    def fake_generate(prompt, inputs, output_path, label, log_parts, **kwargs):
        prompts.append(prompt)
        output_path.write_bytes(png_bytes((10 * len(prompts), 20, 30, 255)))
        server.update_job(job_id, provider="openai", provider_model="test-model", provider_usage={"total_tokens": 5}, provider_usage_available=True)
        return output_path
    reviews = iter([
        {"status": "failed", "issues": ["asset_duplicated"], "repair_instruction": "Remove duplicate furniture.", "provider_invoked": True, "model": "qa-model", "usage": {"total_tokens": 3}},
        {"status": "passed", "issues": [], "repair_instruction": "", "provider_invoked": True, "model": "qa-model", "usage": {"total_tokens": 4}},
    ])
    monkeypatch.setattr(server, "generate_ai_render", fake_generate)
    monkeypatch.setattr(server, "lock_composite_to_source", lambda before, after: {"pixel_coordinate_locked": True})
    monkeypatch.setattr(server, "verify_composition", lambda *args: next(reviews))
    server.interior_compose_worker(job_id, source, [], [], {}, [], "", "flash")
    job = server.get_job(job_id)
    assert job["status"] == "done" and job["ready_for_report"] is True
    assert job["result_revision"] == 2
    assert len(job["provider_calls"]) == len(job["quality_calls"]) == 2
    assert (root / "after-raw-attempt-1.png").is_file()
    assert (root / "after-raw-attempt-2.png").is_file()
    assert "Remove duplicate furniture." in prompts[1]
    assert job["quality_review"]["applied_to"] == "delivered_after"


def test_failed_repair_retains_first_usage_and_marks_second_charge_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "compose")
    job_id = server.create_job()
    root = tmp_path / "compose" / job_id[:8]
    root.mkdir(parents=True)
    source = root / "before.png"
    source.write_bytes(png_bytes())
    server.update_job(job_id, composition_snapshot={"assets": [], "finishes": [], "placements": [],
                                                    "source_sha256": server.file_sha256(source)})
    calls = []
    def fake_generate(prompt, inputs, output_path, label, log_parts, **kwargs):
        calls.append(output_path)
        if len(calls) == 2:
            raise RuntimeError("provider request failed")
        output_path.write_bytes(png_bytes((10, 20, 30, 255)))
        server.update_job(job_id, provider="openai", provider_model="test-model",
                          provider_usage={"total_tokens": 51}, provider_usage_available=True)
        return output_path
    monkeypatch.setattr(server, "generate_ai_render", fake_generate)
    monkeypatch.setattr(server, "lock_composite_to_source", lambda before, after: {"pixel_coordinate_locked": True})
    monkeypatch.setattr(server, "verify_composition", lambda *args: {
        "status": "failed", "issues": ["asset_duplicated"],
        "repair_instruction": "Remove duplicate furniture.", "provider_invoked": True,
        "model": "qa-model", "usage": {"total_tokens": 9},
    })
    server.interior_compose_worker(job_id, source, [], [], {}, [], "", "flash")
    job = server.get_job(job_id)
    assert job["status"] == "error" and job["ready_for_report"] is False
    assert job["provider_calls"][0]["status"] == "completed"
    assert job["provider_calls"][0]["usage"] == {"total_tokens": 51}
    assert job["provider_calls"][1]["attempt"] == 2
    assert job["provider_calls"][1]["status"] == "failed_or_unknown"
    assert job["provider_calls"][1]["usage"] == {}
    assert job["provider_calls"][1]["usage_available"] is False
    assert job["provider_usage"] == {}
    assert job["provider_usage_available"] is False
    assert job["quality_calls"][0]["usage"] == {"total_tokens": 9}
    assert job["result_revision"] == 1
    assert (root / "after-raw-attempt-1.png").is_file()
    assert not (root / "after-raw-attempt-2.png").exists()


def test_unaligned_result_cannot_be_ready_even_if_semantic_qa_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(server.db, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(server, "INTERIOR_COMPOSE_DIR", tmp_path / "compose")
    monkeypatch.setattr(server, "path_to_url", lambda path: f"/test/{path.name}")
    job_id = server.create_job()
    root = tmp_path / "compose" / job_id[:8]
    root.mkdir(parents=True)
    source = root / "before.png"
    source.write_bytes(png_bytes())
    server.update_job(job_id, composition_snapshot={"assets": [], "finishes": [], "placements": [], "source_sha256": server.file_sha256(source)})
    monkeypatch.setattr(server, "generate_ai_render", lambda prompt, inputs, output_path, *args, **kwargs: output_path.write_bytes(png_bytes()))
    monkeypatch.setattr(server, "lock_composite_to_source", lambda before, after: (_ for _ in ()).throw(TypeError("alignment failed")))
    monkeypatch.setattr(server, "align_result_to_source", lambda before, after: None)
    monkeypatch.setattr(server, "verify_composition", lambda *args: {"status": "passed", "issues": [], "repair_instruction": ""})
    server.interior_compose_worker(job_id, source, [], [], {}, [], "", "flash")
    job = server.get_job(job_id)
    assert job["status"] == "done"
    assert job["frame_aligned"] is False
    assert job["ready_for_report"] is False
    assert "pixel_lock_failed:TypeError" in job["warnings"]
