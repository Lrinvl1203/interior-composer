"""Post-generation framing alignment for composition-preserving AI renders.

Image models frequently "re-frame" a studio-style render — same content, but
zoomed or shifted — which breaks the Before/After slider seam.  Both frames in
that comparison are single objects on a near-uniform background, so we align
by content bounding box: scale the generated content to the source content's
box and paste it at the same position on a source-sized canvas.

Photographic inputs degrade gracefully: their content bbox spans essentially
the whole frame on both sides, so the transform is a no-op and we skip.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# 배경 판정: 테두리 대표색과의 밝기 차이가 이 값을 넘으면 콘텐츠 픽셀.
BACKGROUND_TOLERANCE = 14
# 상대 스케일 허용 범위 — 이 밖이면 bbox 오검출로 보고 정렬을 포기한다.
MIN_SCALE, MAX_SCALE = 0.4, 2.5
# 상대 스케일 차이가 이 비율 이하면 이미 정렬된 것으로 본다.
SKIP_BELOW = 0.03


def _border_color(image: Image.Image) -> tuple[int, int, int]:
    """1px 테두리에서 가장 흔한 색 — 스튜디오 렌더의 균일 배경 대표색."""
    width, height = image.size
    strip = Image.new("RGB", (width * 2 + height * 2, 1))
    strip.paste(image.crop((0, 0, width, 1)), (0, 0))
    strip.paste(image.crop((0, height - 1, width, height)), (width, 0))
    strip.paste(image.crop((0, 0, 1, height)).transpose(Image.Transpose.ROTATE_90), (width * 2, 0))
    strip.paste(image.crop((width - 1, 0, width, height)).transpose(Image.Transpose.ROTATE_90), (width * 2 + height, 0))
    colors = strip.getcolors(maxcolors=strip.width)
    return max(colors, key=lambda entry: entry[0])[1] if colors else (0, 0, 0)


def _content_bbox(image: Image.Image, background: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    difference = Image.merge("RGB", [
        band.point(lambda value, ref=background[index]: abs(value - ref))
        for index, band in enumerate(image.split())
    ]).convert("L")
    return difference.point(lambda value: 255 if value > BACKGROUND_TOLERANCE else 0).getbbox()


def align_result_to_source(source_path: Path, result_path: Path) -> bool:
    """결과 이미지를 원본의 콘텐츠 프레이밍에 맞춰 재배치한다. 변경 시 True."""
    with Image.open(source_path) as source_file, Image.open(result_path) as result_file:
        source = source_file.convert("RGB")
        result = result_file.convert("RGB")
    source_bbox = _content_bbox(source, _border_color(source))
    result_background = _border_color(result)
    result_bbox = _content_bbox(result, result_background)
    if not source_bbox or not result_bbox:
        return False
    source_w, source_h = source_bbox[2] - source_bbox[0], source_bbox[3] - source_bbox[1]
    result_w, result_h = result_bbox[2] - result_bbox[0], result_bbox[3] - result_bbox[1]
    if min(source_w, source_h, result_w, result_h) < 8:
        return False

    # 프레임 대비 콘텐츠 비율로 상대 스케일 판단 (이미지 해상도 차이는 무시된다).
    relative_x = (source_w / source.width) / (result_w / result.width)
    relative_y = (source_h / source.height) / (result_h / result.height)
    if not (MIN_SCALE <= relative_x <= MAX_SCALE and MIN_SCALE <= relative_y <= MAX_SCALE):
        return False
    already_aligned = abs(relative_x - 1) <= SKIP_BELOW and abs(relative_y - 1) <= SKIP_BELOW
    if already_aligned and result.size == source.size:
        return False
    if already_aligned:
        result.resize(source.size, Image.Resampling.LANCZOS).save(result_path)
        return True

    content = result.crop(result_bbox).resize((source_w, source_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", source.size, result_background)
    canvas.paste(content, (source_bbox[0], source_bbox[1]))
    canvas.save(result_path)
    return True


def _register_to_source(source, result):
    """Register a generated frame onto the BEFORE pixel coordinate system.

    ECC affine registration corrects the small zoom/translation/rotation drift
    commonly introduced by image-editing models.  Implausibly large transforms
    are rejected so a failed match can never distort the locked camera.
    """
    import cv2
    import numpy as np

    height, width = source.shape[:2]
    if result.shape[:2] != (height, width):
        result = cv2.resize(result, (width, height), interpolation=cv2.INTER_LANCZOS4)
    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    result_gray = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    # Camera alignment only needs large structural features.  Run ECC on a
    # bounded preview so phone photos and 4K renders do not stall the job.
    analysis_scale = min(1.0, 1200.0 / max(width, height))
    if analysis_scale < 1.0:
        analysis_size = (max(1, round(width * analysis_scale)), max(1, round(height * analysis_scale)))
        source_gray = cv2.resize(source_gray, analysis_size, interpolation=cv2.INTER_AREA)
        result_gray = cv2.resize(result_gray, analysis_size, interpolation=cv2.INTER_AREA)
    source_gray = cv2.GaussianBlur(source_gray, (5, 5), 0)
    result_gray = cv2.GaussianBlur(result_gray, (5, 5), 0)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 120, 1e-6)
    try:
        score, warp = cv2.findTransformECC(source_gray, result_gray, warp, cv2.MOTION_AFFINE, criteria)
        scale_x = float((warp[0, 0] ** 2 + warp[0, 1] ** 2) ** 0.5)
        scale_y = float((warp[1, 0] ** 2 + warp[1, 1] ** 2) ** 0.5)
        fit_height, fit_width = source_gray.shape
        translation_ok = abs(float(warp[0, 2])) <= fit_width * 0.12 and abs(float(warp[1, 2])) <= fit_height * 0.12
        transform_ok = 0.88 <= scale_x <= 1.12 and 0.88 <= scale_y <= 1.12 and translation_ok and score >= 0.12
        if not transform_ok:
            return result, False, float(score)
        full_warp = warp.copy()
        full_warp[0, 2] /= analysis_scale
        full_warp[1, 2] /= analysis_scale
        registered = cv2.warpAffine(
            result,
            full_warp,
            (width, height),
            flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT,
        )
        return registered, True, float(score)
    except cv2.error:
        return result, False, 0.0


def _shared_structure_mask(source, registered):
    """Return long source edges that still exist in the edited result.

    Matching a source line against a dilated result edge protects walls,
    floors, ceilings, windows and doors while avoiding the ghost outline of an
    old loose object that the model intentionally removed.
    """
    import cv2
    import numpy as np

    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    result_gray = cv2.cvtColor(registered, cv2.COLOR_RGB2GRAY)
    source_edges = cv2.Canny(source_gray, 55, 155)
    result_edges = cv2.Canny(result_gray, 55, 155)
    nearby_result_edges = cv2.dilate(result_edges, np.ones((9, 9), np.uint8), iterations=1)
    height, width = source_gray.shape
    minimum_length = max(28, int(min(width, height) * 0.16))
    lines = cv2.HoughLinesP(
        source_edges,
        1,
        np.pi / 180,
        threshold=max(24, minimum_length // 3),
        minLineLength=minimum_length,
        maxLineGap=max(8, minimum_length // 8),
    )
    protected = np.zeros_like(source_gray, dtype=np.uint8)
    if lines is not None:
        # OpenCV 4 returns (N, 1, 4); OpenCV 5 may return (N, 4).
        for line in np.asarray(lines).reshape(-1, 4):
            candidate = np.zeros_like(source_gray, dtype=np.uint8)
            x1, y1, x2, y2 = (int(value) for value in line)
            cv2.line(candidate, (x1, y1), (x2, y2), 255, 1, cv2.LINE_AA)
            line_pixels = candidate > 0
            overlap = float((nearby_result_edges[line_pixels] > 0).mean()) if line_pixels.any() else 0.0
            if overlap >= 0.42:
                cv2.line(protected, (x1, y1), (x2, y2), 255, 3, cv2.LINE_AA)
    protected = cv2.dilate(protected, np.ones((3, 3), np.uint8), iterations=1)
    protected[:2, :] = 255
    protected[-2:, :] = 255
    protected[:, :2] = 255
    protected[:, -2:] = 255
    return protected


def lock_composite_to_source(source_path: Path, result_path: Path) -> dict[str, object]:
    """Create a slider-safe AFTER frame on the exact BEFORE coordinate system.

    The source is always the final base canvas.  The generated image contributes
    only meaningful changed pixels; shared architectural lines and all unchanged
    pixels are copied bit-for-bit from BEFORE.  This makes a slider reveal feel
    like furniture/materials appearing inside one fixed photograph.
    """
    import cv2
    import numpy as np

    with Image.open(source_path) as source_file, Image.open(result_path) as result_file:
        source = np.asarray(source_file.convert("RGB"), dtype=np.uint8)
        result = np.asarray(result_file.convert("RGB"), dtype=np.uint8)

    registered, registration_applied, registration_score = _register_to_source(source, result)
    delta = np.max(np.abs(registered.astype(np.int16) - source.astype(np.int16)), axis=2).astype(np.uint8)
    global_change_fraction = float((delta >= 10).mean())
    # Source-edge pasting is safe for a small localized edit, but with broad
    # relighting/material changes it creates a patchwork of old and new frames.
    # Keep the clean affine-registered render in that case. This is a 2D frame
    # registration result, not proof that the AI preserved 3D architecture.
    if global_change_fraction >= 0.35:
        Image.fromarray(registered, mode="RGB").save(result_path, format="PNG")
        exact_pixels = np.all(registered == source, axis=2)
        return {
            "frame_size_locked": True,
            "pixel_coordinate_locked": registration_applied,
            "registration_applied": registration_applied,
            "registration_score": round(registration_score, 4),
            "alignment_method": "affine_registration_full_frame" if registration_applied else "unverified_full_frame",
            "architecture_validated": False,
            "global_change_fraction": round(global_change_fraction, 4),
            "structure_pixel_count": 0,
            "unchanged_pixel_ratio": round(float(exact_pixels.mean()), 4),
            "changed_pixel_ratio": round(float((~exact_pixels).mean()), 4),
        }
    structure_mask = _shared_structure_mask(source, registered)
    changed = np.where(delta >= 10, 255, 0).astype(np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(changed, connectivity=8)
    minimum_area = max(24, int(source.shape[0] * source.shape[1] * 0.00004))
    filtered = np.zeros_like(changed)
    for index in range(1, component_count):
        if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_area:
            filtered[labels == index] = 255
    filtered[structure_mask > 0] = 0

    # Feather only inward. Pixels outside the allowed mask remain exactly equal
    # to BEFORE, which is the invariant required by the comparison slider.
    softened = cv2.GaussianBlur(filtered, (5, 5), 0).astype(np.float32) / 255.0
    alpha = np.where(filtered > 0, softened, 0.0)[..., None]
    composite = np.clip(source.astype(np.float32) * (1.0 - alpha) + registered.astype(np.float32) * alpha, 0, 255).astype(np.uint8)
    composite[structure_mask > 0] = source[structure_mask > 0]
    Image.fromarray(composite, mode="RGB").save(result_path, format="PNG")

    exact_pixels = np.all(composite == source, axis=2)
    return {
        "frame_size_locked": True,
        "pixel_coordinate_locked": True,
        "registration_applied": registration_applied,
        "registration_score": round(registration_score, 4),
        "alignment_method": "localized_source_pixel_lock",
        "architecture_validated": False,
        "global_change_fraction": round(global_change_fraction, 4),
        "structure_pixel_count": int((structure_mask > 0).sum()),
        "unchanged_pixel_ratio": round(float(exact_pixels.mean()), 4),
        "changed_pixel_ratio": round(float((~exact_pixels).mean()), 4),
    }
