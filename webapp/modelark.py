"""Small, dependency-light client for the BytePlus ModelArk APIs.

The project already depends on ``httpx`` through the OpenAI SDK, so image and
video generation use the REST endpoints directly.  Keeping the adapter here
also prevents the API key from ever reaching the browser-facing frontend.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv


BASE = Path(__file__).resolve().parents[1]
DOTENV_PATH = BASE / ".env"
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# Current ap-southeast-1 model versions.  Every value can be overridden in
# .env without a code change when BytePlus publishes a newer version.
DEFAULT_TEXT_MODEL = "seed-2-0-lite-260428"
DEFAULT_REASONING_MODEL = "dola-seed-2-1-turbo-260628"
DEFAULT_IMAGE_MODEL = "seedream-5-0-lite-260128"
DEFAULT_IMAGE_PRO_MODEL = "dola-seedream-5-0-pro-260628"
DEFAULT_VIDEO_MODEL = "seedance-1-5-pro-251215"


class ModelArkError(RuntimeError):
    """Raised for a safe, user-readable ModelArk request failure."""


@dataclass(frozen=True)
class ModelArkSettings:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    text_model: str = DEFAULT_TEXT_MODEL
    reasoning_model: str = DEFAULT_REASONING_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL
    image_pro_model: str = DEFAULT_IMAGE_PRO_MODEL
    video_model: str = DEFAULT_VIDEO_MODEL

    @classmethod
    def from_env(cls, *, required: bool = True) -> "ModelArkSettings":
        load_dotenv(DOTENV_PATH, override=False)
        api_key = os.getenv("ARK_API_KEY", "").strip()
        if required and not api_key:
            raise ModelArkError("ARK_API_KEY가 .env에 설정되어 있지 않습니다.")
        return cls(
            api_key=api_key,
            base_url=os.getenv("ARK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
            text_model=os.getenv("ARK_TEXT_MODEL", DEFAULT_TEXT_MODEL).strip(),
            reasoning_model=os.getenv("ARK_REASONING_MODEL", DEFAULT_REASONING_MODEL).strip(),
            image_model=os.getenv("ARK_IMAGE_MODEL", DEFAULT_IMAGE_MODEL).strip(),
            image_pro_model=os.getenv("ARK_IMAGE_PRO_MODEL", DEFAULT_IMAGE_PRO_MODEL).strip(),
            video_model=os.getenv("ARK_VIDEO_MODEL", DEFAULT_VIDEO_MODEL).strip(),
        )

    def public_summary(self) -> dict[str, Any]:
        """Return configuration metadata without returning the secret key."""

        return {
            "configured": bool(self.api_key),
            "base_url": self.base_url,
            "models": {
                "text": self.text_model,
                "reasoning": self.reasoning_model,
                "image": self.image_model,
                "image_pro": self.image_pro_model,
                "video": self.video_model,
            },
        }


def is_configured() -> bool:
    return bool(ModelArkSettings.from_env(required=False).api_key)


def image_data_url(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Input image not found: {path}")
    media_type, _ = mimetypes.guess_type(path.name)
    media_type = media_type if media_type and media_type.startswith("image/") else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "MODELARK_ERROR")
            message = str(error.get("message") or "ModelArk request failed")
            return f"{code}: {message}"
        message = payload.get("message")
        if message:
            return str(message)
    return f"HTTP {response.status_code}"


def _request_json(
    method: str,
    path: str,
    *,
    settings: ModelArkSettings,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}
    try:
        response = httpx.request(
            method,
            f"{settings.base_url}/{path.lstrip('/')}",
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ModelArkError(f"ModelArk 연결에 실패했습니다: {exc.__class__.__name__}") from exc
    if response.is_error:
        raise ModelArkError(_error_message(response))
    try:
        result = response.json()
    except ValueError as exc:
        raise ModelArkError("ModelArk가 JSON이 아닌 응답을 반환했습니다.") from exc
    if not isinstance(result, dict):
        raise ModelArkError("ModelArk 응답 형식이 올바르지 않습니다.")
    return result


def _safe_download(url: str, output_path: Path, *, max_bytes: int) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ModelArkError("ModelArk 결과 URL이 안전한 HTTPS 주소가 아닙니다.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".part")
    total = 0
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as response:
            if response.is_error:
                raise ModelArkError(f"ModelArk 결과 다운로드 실패: HTTP {response.status_code}")
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ModelArkError("ModelArk 결과 파일이 허용 크기를 초과했습니다.")
                    handle.write(chunk)
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output_path


def generate_image(
    prompt: str,
    image_paths: Iterable[Path] = (),
    *,
    output_path: Path,
    quality: str = "standard",
    settings: ModelArkSettings | None = None,
) -> dict[str, Any]:
    """Generate or edit one image and save it locally.

    ``standard`` uses Seedream 5.0 Lite for cost and speed.  ``pro`` is opt-in
    because it costs more and should be reserved for a selected final render.
    """

    settings = settings or ModelArkSettings.from_env()
    images = [image_data_url(Path(path)) for path in image_paths]
    if len(images) > 14:
        raise ValueError("ModelArk 이미지 입력은 최대 14개까지 지원합니다.")
    if quality not in {"standard", "pro"}:
        raise ValueError("quality는 standard 또는 pro여야 합니다.")
    model = settings.image_pro_model if quality == "pro" else settings.image_model
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": "2K",
        "output_format": "png",
        "response_format": "url",
        "watermark": False,
    }
    if images:
        payload["image"] = images[0] if len(images) == 1 else images
    result = _request_json("POST", "images/generations", settings=settings, payload=payload)
    data = result.get("data")
    url = data[0].get("url") if isinstance(data, list) and data and isinstance(data[0], dict) else None
    if not isinstance(url, str) or not url:
        raise ModelArkError("ModelArk 이미지 응답에 결과 URL이 없습니다.")
    _safe_download(url, output_path, max_bytes=50 * 1024 * 1024)
    return {
        "path": str(Path(output_path)),
        "model": str(result.get("model") or model),
        "size": data[0].get("size"),
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
    }


def create_video_task(
    prompt: str,
    *,
    first_frame: Path | None = None,
    duration: int = 5,
    draft: bool = True,
    generate_audio: bool = False,
    ratio: str = "16:9",
    settings: ModelArkSettings | None = None,
) -> dict[str, Any]:
    """Create an asynchronous Seedance 1.5 Pro task.

    Draft mode is the default cost guard.  The caller must explicitly request a
    normal render after reviewing a draft.
    """

    settings = settings or ModelArkSettings.from_env()
    if not 3 <= duration <= 12:
        raise ValueError("영상 길이는 3~12초 범위여야 합니다.")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if first_frame is not None:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(first_frame)}})
    payload = {
        "model": settings.video_model,
        "content": content,
        "duration": duration,
        "draft": draft,
        "generate_audio": generate_audio,
        "ratio": ratio,
        "watermark": False,
    }
    result = _request_json("POST", "contents/generations/tasks", settings=settings, payload=payload)
    task_id = result.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ModelArkError("ModelArk 영상 응답에 작업 ID가 없습니다.")
    return result


def get_video_task(task_id: str, *, settings: ModelArkSettings | None = None) -> dict[str, Any]:
    settings = settings or ModelArkSettings.from_env()
    if not task_id.startswith("cgt-") or len(task_id) > 160:
        raise ValueError("올바른 ModelArk 영상 작업 ID가 아닙니다.")
    return _request_json("GET", f"contents/generations/tasks/{task_id}", settings=settings)


def wait_for_video(
    task_id: str,
    *,
    output_path: Path | None = None,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 10.0,
    settings: ModelArkSettings | None = None,
) -> dict[str, Any]:
    settings = settings or ModelArkSettings.from_env()
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = get_video_task(task_id, settings=settings)
        status = str(result.get("status") or "")
        if status == "succeeded":
            content = result.get("content")
            url = content.get("video_url") if isinstance(content, dict) else None
            if output_path is not None:
                if not isinstance(url, str) or not url:
                    raise ModelArkError("완료된 영상 작업에 결과 URL이 없습니다.")
                _safe_download(url, output_path, max_bytes=500 * 1024 * 1024)
                result["local_path"] = str(Path(output_path))
            return result
        if status in {"failed", "expired"}:
            error = result.get("error")
            raise ModelArkError(f"영상 생성이 {status} 상태로 종료되었습니다: {error}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"영상 작업 대기 시간이 초과되었습니다: {task_id}")
        time.sleep(max(1.0, poll_seconds))
