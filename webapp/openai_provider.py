"""Single OpenAI boundary: no SDK retries, provider fallback or raw error exports."""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps


BASE = Path(__file__).resolve().parents[1]
DOTENV_PATH = BASE / ".env"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_VISION_MODEL = "gpt-5-mini"


class OpenAIProviderError(RuntimeError):
    """Only application-authored messages and allowlisted usage belong here."""

    def __init__(self, message: str, *, usage: Any = None, model: str | None = None):
        super().__init__(message)
        self.usage = _usage_dict(usage)
        self.model = model


def safe_model_id(value: Any, api_key: str = "") -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        if not api_key or api_key not in value:
            return value
    return None


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = field(repr=False)
    image_model: str = DEFAULT_IMAGE_MODEL
    vision_model: str = DEFAULT_VISION_MODEL

    @classmethod
    def from_env(cls, *, required: bool = True) -> "OpenAISettings":
        load_dotenv(DOTENV_PATH, override=False)
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if required and not api_key:
            raise OpenAIProviderError("OPENAI_API_KEY를 프로젝트 .env에 설정한 뒤 앱을 다시 시작해 주세요.")
        return cls(
            api_key=api_key,
            image_model=os.getenv("OPENAI_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL,
            vision_model=os.getenv("OPENAI_VISION_MODEL", "").strip() or DEFAULT_VISION_MODEL,
        )

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider": "OpenAI", "configured": bool(self.api_key),
            "models": {"image": safe_model_id(self.image_model, self.api_key),
                       "vision": safe_model_id(self.vision_model, self.api_key)},
        }


def is_configured() -> bool:
    return bool(OpenAISettings.from_env(required=False).api_key)


def create_client(settings: OpenAISettings, *, timeout_seconds: float = 300.0) -> OpenAI:
    if not settings.api_key.strip():
        raise OpenAIProviderError("OPENAI_API_KEY를 프로젝트 .env에 설정한 뒤 앱을 다시 시작해 주세요.")
    # Another project's OPENAI_BASE_URL must never receive this project's key.
    return OpenAI(api_key=settings.api_key, base_url="https://api.openai.com/v1",
                  timeout=timeout_seconds, max_retries=0)


def _png_upload(path: Path, name: str, max_edge: int = 2048) -> tuple[str, BytesIO, str]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Input image not found: {path}")
    with Image.open(path) as source:
        prepared = ImageOps.exif_transpose(source).convert("RGBA")
        prepared.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        data = BytesIO()
        prepared.save(data, "PNG", optimize=True)
    data.seek(0)
    return (name, data, "image/png")


def _usage_dict(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        dump = getattr(usage, "model_dump", None)
        usage = dump() if callable(dump) else {}
    if not isinstance(usage, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        if type(usage.get(key)) is int and usage[key] >= 0:
            result[key] = usage[key]
    for key in ("prompt_tokens_details", "completion_tokens_details", "input_tokens_details", "output_tokens_details"):
        details = usage.get(key)
        if isinstance(details, dict):
            cleaned = {name: details[name] for name in ("cached_tokens", "reasoning_tokens", "image_tokens", "text_tokens")
                       if type(details.get(name)) is int and details[name] >= 0}
            if cleaned:
                result[key] = cleaned
    return result


def generate_image(
    prompt: str,
    image_paths: Iterable[Path] = (),
    *,
    output_path: Path,
    quality: str = "high",
    settings: OpenAISettings | None = None,
) -> dict[str, Any]:
    """One billed generate/edit attempt; low/high share the configured model."""

    settings = settings or OpenAISettings.from_env()
    paths = [Path(path) for path in image_paths]
    if len(paths) > 16:
        raise ValueError("OpenAI 이미지 입력은 최대 16개까지 지원합니다.")
    if quality not in {"low", "medium", "high"}:
        raise ValueError("OpenAI 이미지 품질은 low, medium 또는 high여야 합니다.")
    uploads = [_png_upload(path, f"reference_{index:02d}.png") for index, path in enumerate(paths, start=1)]
    client = create_client(settings)
    common = {
        "model": settings.image_model,
        "prompt": prompt,
        "quality": quality,
        "size": "auto",
        "output_format": "png",
    }
    try:
        if paths:
            # GPT Image 2 always uses high input fidelity. Omit the legacy option.
            # Model-default background; asset extraction restores alpha locally.
            response = client.images.edit(image=uploads, **common)
        else:
            response = client.images.generate(**common)
    except Exception:
        raise OpenAIProviderError("OpenAI 이미지 요청에 실패했습니다. 키, 모델 접근 권한과 사용 한도를 확인해 주세요.") from None
    usage = _usage_dict(getattr(response, "usage", None))
    model = safe_model_id(settings.image_model, settings.api_key)
    try:
        encoded = response.data[0].b64_json if response.data else None
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("Missing image")
        image_bytes = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(image_bytes)) as decoded:
            decoded.verify()
    except Exception:
        raise OpenAIProviderError("OpenAI 이미지 응답에 유효한 이미지가 없습니다.", usage=usage, model=model) from None
    output_path = Path(output_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
    except OSError:
        raise OpenAIProviderError("OpenAI 결과 이미지를 로컬에 저장하지 못했습니다.", usage=usage, model=model) from None
    return {
        "path": str(output_path),
        "model": model, "quality": quality, "provider": "openai", "usage": usage,
    }
