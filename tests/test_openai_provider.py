"""OpenAI-only contracts. Every SDK request uses an in-memory HTTP transport."""
import ast
import base64
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI as RealOpenAI
from PIL import Image

from services import ai_asset
from webapp import openai_provider as provider, server


def _image_bytes():
    data = io.BytesIO()
    Image.new("RGBA", (32, 24), (120, 90, 70, 0)).save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def image_sdk(monkeypatch):
    state = {"calls": [], "clients": [], "status": 200}

    def transport(request):
        state["calls"].append({"url": str(request.url), "body": request.read(),
                               "content_type": request.headers.get("content-type", "")})
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "test-api-secret /private/token-path", "type": "server_error"}})
        return httpx.Response(200, json={"created": 1, "data": [{"b64_json": state.get("encoded", base64.b64encode(_image_bytes()).decode())}],
                                       "usage": {"input_tokens": 15, "output_tokens": 25, "total_tokens": 40,
                                                 "input_tokens_details": {"image_tokens": 10, "text_tokens": 5},
                                                 "debug": "test-api-secret"}})

    def client(**kwargs):
        state["clients"].append(kwargs)
        return RealOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

    monkeypatch.setattr(provider, "OpenAI", client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-secret")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setenv("OPENAI_VISION_MODEL", "gpt-5-mini")
    return state


@pytest.mark.parametrize("quality", ["low", "high"])
def test_image_generate_uses_official_sdk_quality_and_safe_usage(image_sdk, tmp_path, quality):
    result = provider.generate_image("A room", output_path=tmp_path / "out.png", quality=quality)
    assert (tmp_path / "out.png").read_bytes() == _image_bytes()
    assert len(image_sdk["calls"]) == 1
    call = image_sdk["calls"][0]
    assert call["url"] == "https://api.openai.com/v1/images/generations"
    assert json.loads(call["body"]) == {"model": "gpt-image-2", "prompt": "A room", "quality": quality,
                                       "size": "auto", "output_format": "png"}
    assert image_sdk["clients"][0]["max_retries"] == 0
    assert result["provider"] == "openai"
    assert result["usage"]["total_tokens"] == 40
    assert result["usage"]["input_tokens_details"] == {"image_tokens": 10, "text_tokens": 5}
    assert "test-api-secret" not in repr(result)
    assert "cost" not in result


@pytest.mark.parametrize("quality", ["low", "high"])
def test_image_edit_sends_ordered_png_references_without_unsupported_options(image_sdk, tmp_path, quality):
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (12, 10), "red").save(first)
    Image.new("RGB", (12, 10), "blue").save(second)
    provider.generate_image("Keep identities", [first, second], output_path=tmp_path / "out.png", quality=quality)
    call = image_sdk["calls"][0]
    assert call["url"] == "https://api.openai.com/v1/images/edits"
    assert call["content_type"].startswith("multipart/form-data")
    body = call["body"]
    assert body.index(b'filename="reference_01.png"') < body.index(b'filename="reference_02.png"')
    assert body.count(b"Content-Type: image/png") == 2
    assert b'name="quality"\r\n\r\n' + quality.encode() in body
    assert b"input_fidelity" not in body
    assert b'name="background"' not in body


def test_sdk_errors_are_one_attempt_safe_and_do_not_write_results(image_sdk, tmp_path):
    image_sdk["status"] = 500
    with pytest.raises(provider.OpenAIProviderError) as caught:
        provider.generate_image("A room", output_path=tmp_path / "out.png")
    assert len(image_sdk["calls"]) == 1
    assert not (tmp_path / "out.png").exists()
    assert "test-api-secret" not in str(caught.value)
    assert "token-path" not in str(caught.value)
    assert caught.value.usage == {}


def test_malformed_image_retains_reported_usage_but_never_writes_file(image_sdk, tmp_path):
    image_sdk["encoded"] = "invalid base64"
    with pytest.raises(provider.OpenAIProviderError) as caught:
        provider.generate_image("A room", output_path=tmp_path / "out.png")
    assert caught.value.usage["total_tokens"] == 40
    assert caught.value.model == "gpt-image-2"
    assert not (tmp_path / "out.png").exists()


@pytest.mark.parametrize("mode,expected_quality", [("flash", "low"), ("premium", "high")])
def test_legacy_render_modes_are_both_openai(image_sdk, tmp_path, mode, expected_quality):
    job_id = server.create_job()
    server.generate_ai_render("room", [], tmp_path / "out.png", "render", [], job_id=job_id, ai_mode=mode)
    assert json.loads(image_sdk["calls"][0]["body"])["quality"] == expected_quality
    job = server.get_job(job_id)
    assert job["provider"] == "openai"
    assert job["provider_quality"] == expected_quality
    assert job["provider_usage"]["total_tokens"] == 40


@pytest.mark.parametrize("mode,expected_quality", [("flash", "low"), ("premium", "high")])
def test_asset_extraction_uses_same_configured_openai_model_and_two_refs(image_sdk, monkeypatch, mode, expected_quality):
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2-test-snapshot")
    source = Image.new("RGBA", (80, 60), "white")
    result = ai_asset.extract_selected_asset(source, "Oak chair", "test-api-secret", bbox=(5, 5, 60, 50), ai_mode=mode)
    assert len(image_sdk["calls"]) == 1
    body = image_sdk["calls"][0]["body"]
    assert b"gpt-image-2-test-snapshot" in body
    assert body.count(b"Content-Type: image/png") == 2
    assert b'name="quality"\r\n\r\n' + expected_quality.encode() in body
    assert b"input_fidelity" not in body and b'name="background"' not in body
    metadata = result.info["provider_metadata"]
    assert metadata["provider"] == "openai"
    assert metadata["quality"] == expected_quality
    assert metadata["usage"]["total_tokens"] == 40


def test_asset_opaque_output_uses_existing_local_alpha_fallback(image_sdk, monkeypatch):
    opaque = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(opaque, "PNG")
    image_sdk["encoded"] = base64.b64encode(opaque.getvalue()).decode()
    calls = []
    def local_remove(image):
        calls.append(image.size)
        return Image.new("RGBA", image.size, (0, 0, 0, 0))
    monkeypatch.setitem(sys.modules, "services.bg_remove", SimpleNamespace(remove_background=local_remove))
    result = ai_asset.extract_selected_asset(Image.new("RGB", (80, 60)), "chair", "test-api-secret", bbox=(5, 5, 60, 50))
    assert calls == [(32, 24)]
    assert result.getchannel("A").getextrema() == (0, 0)


@pytest.mark.parametrize("mode", ["flash", "premium"])
def test_ark_key_never_enables_generation_or_extraction(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("ARK_API_KEY", "not-an-openai-key")
    monkeypatch.setattr(provider, "OpenAI", lambda **kw: pytest.fail("No key must not create a client"))
    with pytest.raises(server.ProviderNotConfiguredError, match="OPENAI_API_KEY"):
        server.generate_ai_render("room", [], tmp_path / "out.png", "render", [], ai_mode=mode)
    source = tmp_path / "source.png"
    Image.new("RGB", (80, 60)).save(source)
    job_id = server.create_job()
    server.ai_asset_extract_worker(job_id, source, (5, 5, 60, 50), "chair", mode, False, "", "", "", "source.png")
    job = server.get_job(job_id)
    assert job["error_code"] == "PROVIDER_NOT_CONFIGURED"
    assert "OPENAI_API_KEY" in job["error"]
    assert "not-an-openai-key" not in repr(job)
    status = TestClient(server.app).get("/api/ai-providers/status").json()
    assert status["provider"] == "OpenAI" and status["configured"] is False
    assert status["modes"][mode]["configured"] is False


def test_public_status_has_one_provider_and_no_credentials(image_sdk):
    status = TestClient(server.app).get("/api/ai-providers/status").json()
    assert status["provider"] == "OpenAI" and status["configured"] is True
    assert status["models"] == {"image": "gpt-image-2", "vision": "gpt-5-mini"}
    assert status["modes"]["flash"]["quality"] == "low"
    assert status["modes"]["premium"]["quality"] == "high"
    assert all(row["provider"] == "OpenAI" for row in status["modes"].values())
    assert "test-api-secret" not in repr(status)
    assert "test-api-secret" not in repr(provider.OpenAISettings.from_env())
    assert image_sdk["calls"] == []


def test_asset_local_alpha_failure_retains_usage_without_exposing_error(image_sdk, monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (80, 60)).save(source)
    def local_failure(image):
        raise RuntimeError("private-machine-path test-api-secret")
    monkeypatch.setattr(ai_asset, "_clean_transparency", local_failure)
    job_id = server.create_job()
    server.ai_asset_extract_worker(job_id, source, (5, 5, 60, 50), "chair", "flash", False, "", "", "", "source.png")
    job = server.get_job(job_id)
    assert job["status"] == "error"
    assert len(image_sdk["calls"]) == 1
    assert job["provider_usage"]["total_tokens"] == 40
    assert job["provider_calls"][0]["usage"]["total_tokens"] == 40
    assert job["provider_calls"][0]["usage_available"] is True
    assert "private-machine-path" not in repr(job)
    assert "test-api-secret" not in repr(job)


def test_composition_failed_image_response_retains_known_attempt_usage(image_sdk, tmp_path):
    image_sdk["encoded"] = "malformed image"
    source = tmp_path / "source.png"
    Image.new("RGB", (80, 60)).save(source)
    job_id = server.create_job()
    server.interior_compose_worker(job_id, source, [], [], {}, [], "", "flash")
    job = server.get_job(job_id)
    assert job["status"] == "error"
    assert len(image_sdk["calls"]) == 1
    assert job["provider_calls"][0]["status"] == "failed_or_unknown"
    assert job["provider_calls"][0]["usage"]["total_tokens"] == 40
    assert job["provider_calls"][0]["usage_available"] is True
    assert job["ready_for_report"] is False


def test_official_origin_is_pinned_despite_ambient_base_url(image_sdk, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.invalid/v1")
    provider.generate_image("room", output_path=tmp_path / "out.png")
    assert image_sdk["calls"][0]["url"] == "https://api.openai.com/v1/images/generations"


def test_active_modules_do_not_import_or_reference_historical_provider():
    root = Path(__file__).resolve().parents[1]
    for name in ("webapp/server.py", "webapp/openai_provider.py", "webapp/composition_quality.py", "room-asset-composer/services/ai_asset.py"):
        source = (root / name).read_text(encoding="utf-8")
        ast.parse(source)
        assert "modelark" not in source.lower()
        assert "byteplus" not in source.lower()
        assert "ARK_API_KEY" not in source
