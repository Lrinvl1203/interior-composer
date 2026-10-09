"""SAM 2.1 automatic object discovery and transparent PNG extraction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "sam2.1_s.pt"
_model = None


@dataclass
class ObjectMask:
    mask: np.ndarray
    area: int
    bbox: tuple[int, int, int, int]


def runtime_status() -> tuple[bool, str]:
    if not MODEL_PATH.exists():
        return False, f"체크포인트를 찾을 수 없습니다: {MODEL_PATH}"
    try:
        import torch
        from ultralytics import SAM  # noqa: F401
    except Exception as exc:
        return False, f"SAM 2.1 실행 패키지가 없습니다: {exc}"
    device = "CUDA GPU" if torch.cuda.is_available() else "CPU"
    return True, f"SAM 2.1 Small · {device}"


def _get_model():
    global _model
    if _model is None:
        from ultralytics import SAM

        _model = SAM(str(MODEL_PATH))
    return _model


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def discover_objects(
    image: Image.Image,
    min_area_ratio: float = 0.001,
    max_objects: int = 80,
) -> list[ObjectMask]:
    """Run SAM 2.1's automatic mask generator and return useful object candidates."""
    import torch

    source = ImageOps.exif_transpose(image).convert("RGB")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        results = _get_model()(source, device=device, verbose=False)
    except RuntimeError as exc:
        if "torchvision::nms" in str(exc) or "Couldn't load custom C++ ops" in str(exc):
            raise RuntimeError(
                "torch와 torchvision 버전이 맞지 않아 자동 마스크를 생성할 수 없습니다. "
                "requirements-sam21.txt 기준으로 두 패키지를 다시 설치해 주세요."
            ) from exc
        raise

    result_masks = results[0].masks
    if result_masks is None:
        return []
    arrays = result_masks.data.detach().cpu().numpy() > 0.5
    minimum = max(16, round(source.width * source.height * min_area_ratio))
    objects: list[ObjectMask] = []
    for raw in arrays:
        mask = raw
        if mask.shape != (source.height, source.width):
            mask = np.asarray(
                Image.fromarray(mask.astype(np.uint8) * 255).resize(source.size, Image.Resampling.NEAREST)
            ) > 0
        area = int(mask.sum())
        if area >= minimum and area < source.width * source.height * 0.98:
            objects.append(ObjectMask(mask=mask, area=area, bbox=_bbox(mask)))
    objects.sort(key=lambda item: item.area, reverse=True)
    return objects[:max_objects]


def predict_object(
    image: Image.Image,
    points: list[tuple[int, int]],
    labels: list[int],
) -> ObjectMask:
    """Create one refined mask from foreground(1) and background(0) clicks."""
    import torch

    if not points or len(points) != len(labels):
        raise ValueError("선택 점과 라벨이 필요합니다.")
    source = ImageOps.exif_transpose(image).convert("RGB")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    results = _get_model()(
        source,
        points=[[[int(x), int(y)] for x, y in points]],
        labels=[[int(label) for label in labels]],
        device=device,
        verbose=False,
    )
    result_masks = results[0].masks
    if result_masks is None or not len(result_masks.data):
        raise RuntimeError("선택 점에서 객체 마스크를 만들지 못했습니다.")
    mask = result_masks.data[0].detach().cpu().numpy() > 0.5
    if mask.shape != (source.height, source.width):
        mask = np.asarray(
            Image.fromarray(mask.astype(np.uint8) * 255).resize(source.size, Image.Resampling.NEAREST)
        ) > 0
    area = int(mask.sum())
    if not area:
        raise RuntimeError("빈 마스크가 생성됐습니다. + 점 위치를 바꿔주세요.")
    return ObjectMask(mask=mask, area=area, bbox=_bbox(mask))


def masks_at_point(objects: list[ObjectMask], x: int, y: int) -> list[int]:
    hits = [index for index, obj in enumerate(objects) if obj.mask[y, x]]
    return sorted(hits, key=lambda index: objects[index].area)


def make_overlay(image: Image.Image, objects: list[ObjectMask], selected: int | None = None) -> Image.Image:
    base = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    palette = np.asarray([
        (57, 255, 20), (0, 229, 255), (255, 35, 193), (255, 235, 59),
        (124, 77, 255), (255, 109, 0), (0, 255, 163), (255, 64, 64),
    ], dtype=np.float32)
    rgb = base[:, :, :3].astype(np.float32)
    for index, obj in enumerate(objects):
        color = np.asarray((255, 255, 255) if index == selected else palette[index % len(palette)])
        alpha = 0.58 if index == selected else 0.30
        rgb[obj.mask] = rgb[obj.mask] * (1 - alpha) + color * alpha
    base[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return Image.fromarray(base, "RGBA")


def extract_object(image: Image.Image, obj: ObjectMask, feather: int = 1, crop: bool = True) -> Image.Image:
    source = ImageOps.exif_transpose(image).convert("RGBA")
    alpha = Image.fromarray(obj.mask.astype(np.uint8) * 255, "L")
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    source.putalpha(alpha)
    if crop:
        box = alpha.getbbox()
        if box:
            source = source.crop(box)
    return source


def mask_svg_path(mask: np.ndarray) -> str:
    """Convert a binary mask to a compact SVG path suitable for true shape hover."""
    import cv2

    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    parts: list[str] = []
    for contour in contours:
        if len(contour) < 3:
            continue
        perimeter = cv2.arcLength(contour, True)
        points = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.0015), True)
        coords = points.reshape(-1, 2)
        if len(coords) < 3:
            continue
        parts.append(
            "M" + " L".join(f"{int(x)} {int(y)}" for x, y in coords) + " Z"
        )
    return " ".join(parts)
