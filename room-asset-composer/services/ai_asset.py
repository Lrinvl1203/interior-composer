"""Generate clean transparent interior assets with the OpenAI Image API."""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps

from .sam_auto import ObjectMask, extract_object


MODEL = "gpt-image-1.5"


def _png_file(image: Image.Image, name: str, max_edge: int = 1600):
    prepared = ImageOps.exif_transpose(image).convert("RGBA")
    prepared.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    data = BytesIO()
    prepared.save(data, "PNG", optimize=True)
    data.seek(0)
    return (name, data, "image/png")


def _decode(response) -> Image.Image:
    payload = response.data[0].b64_json
    if not payload:
        raise RuntimeError("이미지 API 응답에 이미지 데이터가 없습니다.")
    result = Image.open(BytesIO(base64.b64decode(payload)))
    result.load()
    return result.convert("RGBA")


def _clean_transparency(image: Image.Image) -> Image.Image:
    """Remove opaque or broad semi-transparent backdrops while keeping real object alpha."""

    result = image.convert("RGBA")
    alpha_histogram = result.getchannel("A").histogram()
    total = max(1, result.width * result.height)
    visible_ratio = sum(alpha_histogram[17:]) / total
    opaque_ratio = alpha_histogram[255] / total
    has_opaque_background = alpha_histogram[255] == total
    has_broad_translucent_backdrop = visible_ratio - opaque_ratio > 0.04
    if has_opaque_background or has_broad_translucent_backdrop:
        from . import bg_remove

        return bg_remove.remove_background(result)
    return result


def _client(api_key: str):
    from openai import OpenAI

    if not api_key.strip():
        raise ValueError("OpenAI API 키를 입력해 주세요.")
    return OpenAI(api_key=api_key.strip(), timeout=180)


def _modelark_edit(images: list[Image.Image], prompt: str, quality: str) -> Image.Image:
    """Run the same asset prompt through ModelArk, then restore transparency locally."""

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from webapp.modelark import generate_image

    with TemporaryDirectory(prefix="asset-modelark-") as temporary:
        root = Path(temporary)
        inputs: list[Path] = []
        for index, image in enumerate(images, start=1):
            path = root / f"reference_{index:02d}.png"
            image.convert("RGBA").save(path, "PNG")
            inputs.append(path)
        output = root / "result.png"
        result = generate_image(prompt, inputs, output_path=output, quality="pro" if quality == "high" else "standard")
        generated = Image.open(output); generated.load()
        cleaned = _clean_transparency(generated)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        cleaned.info["provider_metadata"] = {"provider": "modelark", "model": result.get("model"), "usage": usage, "usage_available": bool(usage)}
        return cleaned


def _provider_edit(images: list[Image.Image], prompt: str, api_key: str, quality: str, ai_mode: str) -> Image.Image:
    mode = str(ai_mode or "premium").strip().lower()
    if mode == "flash":
        return _modelark_edit(images, prompt, quality)
    if mode != "premium":
        raise ValueError("AI 모드는 flash 또는 premium이어야 합니다.")
    response = _client(api_key).images.edit(
        model=MODEL,
        image=[_png_file(image, f"reference_{index:02d}.png") for index, image in enumerate(images, start=1)],
        prompt=prompt,
        background="transparent",
        input_fidelity="high",
        output_format="png",
        quality=quality,
        size="1024x1024",
    )
    cleaned = _clean_transparency(_decode(response))
    usage = getattr(response, "usage", None)
    if usage is not None and callable(getattr(usage, "model_dump", None)):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}
    cleaned.info["provider_metadata"] = {"provider": "openai", "model": MODEL, "usage": usage, "usage_available": bool(usage)}
    return cleaned


def complete_sam_asset(
    original: Image.Image,
    obj: ObjectMask,
    description: str,
    api_key: str,
    quality: str = "medium",
    ai_mode: str = "premium",
) -> Image.Image:
    """Preserve a SAM-selected object while reconstructing occluded/missing parts."""
    rough = extract_object(original, obj, feather=1, crop=True)
    x0, y0, x1, y1 = obj.bbox
    pad = round(max(x1 - x0, y1 - y0) * 0.22)
    context = original.crop((
        max(0, x0 - pad), max(0, y0 - pad),
        min(original.width, x1 + pad), min(original.height, y1 + pad),
    ))
    prompt = f"""
Create one production-ready transparent PNG interior asset from the selected object.
Target: {description or 'the main furniture object shown in the transparent cutout'}.
The first reference is scene context. The second is the exact SAM-selected visible pixels.
Preserve the original design, proportions, count, camera angle, materials, colors, wood grain,
upholstery, and lighting as faithfully as possible. Remove all unrelated room background,
walls, floor, plants, decorations, people, and unselected furniture. Reconstruct only missing,
occluded, clipped, or damaged portions needed to make the object a physically complete usable
interior asset. Repair ragged mask edges and stray pixels. Do not redesign or beautify it.
Keep natural contact shadows only when attached to the object. Center it with generous padding.
Output a single isolated object on a fully transparent background, with no text and no border.
""".strip()
    return _provider_edit([context, rough], prompt, api_key, quality, ai_mode)


def extract_selected_asset(
    original: Image.Image,
    description: str,
    api_key: str,
    quality: str = "high",
    bbox: tuple[int, int, int, int] | None = None,
    ai_mode: str = "premium",
) -> Image.Image:
    """Extract one box-selected object without redesigning its identity."""
    if bbox is None:
        raise ValueError("AI 추출 영역을 사각 박스로 지정해 주세요.")
    if not description.strip():
        raise ValueError("선택한 에셋이 무엇인지 텍스트로 설명해 주세요.")

    source = ImageOps.exif_transpose(original).convert("RGBA")
    x0, y0, x1, y1 = (int(value) for value in bbox)
    x0, x1 = sorted((max(0, x0), min(source.width, x1)))
    y0, y1 = sorted((max(0, y0), min(source.height, y1)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("AI 추출 영역을 에셋 전체가 들어가도록 크게 지정해 주세요.")

    guide = source.copy()
    line = max(5, round(max(source.size) * 0.006))
    ImageDraw.Draw(guide).rectangle((x0, y0, x1, y1), outline=(255, 220, 0, 255), width=line)
    prompt = f"""
Extract exactly one interior object enclosed by the yellow rectangle from the reference photo.
The user identifies the target as: {description.strip()}.
This is identity-preserving extraction, not a redesign and not a new inspired product.
Preserve the selected object's exact silhouette, proportions, construction, count, camera angle,
color, material, upholstery, wood grain, seams, handles, legs, hardware, wear, and distinctive
details from the original. Remove every unselected object, person, wall, floor, shadow backdrop,
plant, decoration, and room background. Repair only genuinely occluded, clipped, or damaged
portions needed to make the same object usable; never invent a different design. Keep fine edges,
openings between legs, and transparent or reflective parts natural. Center the one complete object
with generous padding. Output a production-ready PNG on a fully transparent background with no
text, border, floor plane, colored backdrop, or additional object.
""".strip()
    return _provider_edit([source, guide], prompt, api_key, quality, ai_mode)


def generate_direct_asset(
    original: Image.Image,
    description: str,
    api_key: str,
    quality: str = "medium",
    bbox: tuple[int, int, int, int] | None = None,
    points: list[tuple[int, int]] | None = None,
    labels: list[int] | None = None,
    ai_mode: str = "premium",
) -> Image.Image:
    """Create a clean inspired asset directly from the room reference image."""
    if not description.strip() and bbox is None and not points:
        raise ValueError("박스로 대상을 지정하거나 에셋을 설명해 주세요.")
    source = ImageOps.exif_transpose(original).convert("RGBA")
    guide = source.copy()
    draw = ImageDraw.Draw(guide)
    line = max(5, round(max(source.size) * 0.006))
    if bbox is not None:
        draw.rectangle(bbox, outline=(255, 220, 0, 255), width=line)
    for (x, y), label in zip(points or [], labels or []):
        radius = line * 2
        color = (57, 255, 20, 255) if label else (255, 40, 60, 255)
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline="white", width=max(2, line//3))

    selection_instruction = (
        "The target is enclosed by the yellow rectangle. Green dots mean include; red dots mean exclude."
        if bbox is not None else
        "Green dots indicate the target to include; red dots indicate items to exclude."
        if points else
        "Use the written target description to identify the object."
    )
    prompt = f"""
Using the room photo as a visual reference, create a new production-ready interior asset:
{description.strip() or 'the main coherent interior object indicated by the visual selection'}.
{selection_instruction}
Keep the reference object's overall mood, design language, material, texture, color palette,
and approximate camera angle. Exact product identity is not required. Produce a visually clean,
physically complete version: restore hidden legs and occluded parts, remove flowers, vases,
plants, walls, floor, and every unrelated object. Do not include extra furniture beyond the
description. Center one coherent asset with generous padding. Preserve realistic photographic
lighting and material texture. Output a single isolated object on a fully transparent background,
with no text, room, floor plane, border, or colored backdrop.
""".strip()
    return _provider_edit([source, guide], prompt, api_key, quality, ai_mode)
