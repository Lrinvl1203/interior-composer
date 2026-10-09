"""OpenAI provider used by the user-selectable Premium mode."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps


BASE = Path(__file__).resolve().parents[1]
DOTENV_PATH = BASE / ".env"
DEFAULT_REASONING_MODEL = "gpt-5.6-sol"
DEFAULT_IMAGE_MODEL = "gpt-image-2"


class OpenAIProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    reasoning_model: str = DEFAULT_REASONING_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL

    @classmethod
    def from_env(cls, *, required: bool = True) -> "OpenAISettings":
        load_dotenv(DOTENV_PATH, override=False)
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if required and not api_key:
            raise OpenAIProviderError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")
        return cls(
            api_key=api_key,
            reasoning_model=os.getenv("OPENAI_REASONING_MODEL", DEFAULT_REASONING_MODEL).strip(),
            image_model=os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip(),
        )

    def public_summary(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "models": {"reasoning": self.reasoning_model, "image": self.image_model},
        }


def is_configured() -> bool:
    return bool(OpenAISettings.from_env(required=False).api_key)


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
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    dump = getattr(usage, "model_dump", None)
    return dump() if callable(dump) else {}


def generate_image(
    prompt: str,
    image_paths: Iterable[Path] = (),
    *,
    output_path: Path,
    settings: OpenAISettings | None = None,
) -> dict[str, Any]:
    """Generate or edit one high-quality image with the OpenAI Image API."""

    settings = settings or OpenAISettings.from_env()
    paths = [Path(path) for path in image_paths]
    if len(paths) > 16:
        raise ValueError("OpenAI 이미지 입력은 최대 16개까지 지원합니다.")
    client = OpenAI(api_key=settings.api_key, timeout=300.0)
    common = {
        "model": settings.image_model,
        "prompt": prompt,
        "quality": "high",
        "size": "auto",
        "output_format": "png",
    }
    try:
        if paths:
            uploads = [_png_upload(path, f"reference_{index:02d}.png") for index, path in enumerate(paths, start=1)]
            edit_options: dict[str, Any] = {"image": uploads, **common}
            # GPT Image 2 already processes every input at high fidelity and
            # rejects the legacy input_fidelity option.
            if settings.image_model != "gpt-image-2":
                edit_options["input_fidelity"] = "high"
            response = client.images.edit(**edit_options)
        else:
            response = client.images.generate(**common)
    except Exception as exc:
        raise OpenAIProviderError(f"OpenAI 이미지 생성에 실패했습니다: {exc}") from exc
    encoded = response.data[0].b64_json if response.data else None
    if not encoded:
        raise OpenAIProviderError("OpenAI 이미지 응답에 결과 데이터가 없습니다.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(encoded))
    return {
        "path": str(output_path),
        "model": settings.image_model,
        "usage": _usage_dict(getattr(response, "usage", None)),
    }
