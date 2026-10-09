from pathlib import Path

from PIL import Image

from webapp import modelark


def _settings() -> modelark.ModelArkSettings:
    return modelark.ModelArkSettings(api_key="test-secret")


def test_public_summary_never_exposes_api_key() -> None:
    summary = _settings().public_summary()

    assert summary["configured"] is True
    assert summary["models"]["text"] == modelark.DEFAULT_TEXT_MODEL
    assert "test-secret" not in repr(summary)


def test_generate_image_uses_lite_and_multiple_data_urls(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (8, 8), "white").save(first)
    Image.new("RGB", (8, 8), "navy").save(second)
    captured = {}

    def fake_request(method, path, *, settings, payload=None, timeout_seconds=300.0):
        captured.update(method=method, path=path, settings=settings, payload=payload)
        return {"model": settings.image_model, "data": [{"url": "https://example.com/result.png", "size": "2K"}]}

    def fake_download(url, output_path, *, max_bytes):
        Path(output_path).write_bytes(b"image")
        return Path(output_path)

    monkeypatch.setattr(modelark, "_request_json", fake_request)
    monkeypatch.setattr(modelark, "_safe_download", fake_download)
    output = tmp_path / "result.png"

    result = modelark.generate_image("preserve geometry", [first, second], output_path=output, settings=_settings())

    assert captured["method"] == "POST"
    assert captured["path"] == "images/generations"
    assert captured["payload"]["model"] == modelark.DEFAULT_IMAGE_MODEL
    assert len(captured["payload"]["image"]) == 2
    assert all(value.startswith("data:image/png;base64,") for value in captured["payload"]["image"])
    assert captured["payload"]["watermark"] is False
    assert output.is_file()
    assert result["model"] == modelark.DEFAULT_IMAGE_MODEL


def test_video_defaults_to_cost_saving_draft_mode(monkeypatch) -> None:
    captured = {}

    def fake_request(method, path, *, settings, payload=None, timeout_seconds=300.0):
        captured.update(method=method, path=path, settings=settings, payload=payload)
        return {"id": "cgt-20260719-test", "status": "queued"}

    monkeypatch.setattr(modelark, "_request_json", fake_request)

    result = modelark.create_video_task("slow camera push-in", settings=_settings())

    assert result["id"].startswith("cgt-")
    assert captured["path"] == "contents/generations/tasks"
    assert captured["payload"]["model"] == modelark.DEFAULT_VIDEO_MODEL
    assert captured["payload"]["draft"] is True
    assert captured["payload"]["generate_audio"] is False
    assert captured["payload"]["watermark"] is False
