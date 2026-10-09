import importlib.util
import os
import sys
import types
from pathlib import Path

import onnxruntime as ort
from PIL import Image, ImageOps


_sam_session = None
_SamSession = None

# Backend chain: SAM 2.1 (ultralytics, cuda→cpu auto) preferred,
# rembg SAM v1 (ONNX CPU) as fallback for machines without torch/ultralytics.
_backend = None
_sam21_model = None


def _try_init_sam21():
    """Return the torch device string if SAM 2.1 is usable, else None."""
    global _sam21_model
    try:
        import torch
        from ultralytics import SAM
    except Exception:
        return None
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        # Resolve within this standalone tree regardless of the process working directory.
        _sam21_model = SAM(str(Path(__file__).resolve().parents[1] / "sam2.1_s.pt"))
        return device
    except Exception:
        _sam21_model = None
        return None


def _get_backend() -> tuple[str, str]:
    global _backend
    if _backend is None:
        device = _try_init_sam21()
        _backend = ("sam2.1", device) if device else ("rembg-sam-v1", "cpu")
        print(f"SAM backend: {_backend[0]} ({_backend[1]})")
    return _backend


def _extract_sam21(source: Image.Image, points: list[tuple[int, int]], device: str) -> Image.Image:
    import numpy as np

    results = _sam21_model(
        source.convert("RGB"),
        points=[[[int(x), int(y)] for x, y in points]],
        labels=[[1] * len(points)],
        device=device,
        verbose=False,
    )
    masks = results[0].masks
    if masks is None or masks.data.shape[0] == 0:
        return source

    union = masks.data.any(dim=0).cpu().numpy().astype(np.uint8) * 255
    mask_img = Image.fromarray(union, mode="L")
    if mask_img.size != source.size:
        mask_img = mask_img.resize(source.size, Image.NEAREST)
    empty = Image.new("RGBA", source.size, 0)
    return Image.composite(source, empty, mask_img)


def _load_sam_session_class():
    global _SamSession
    if _SamSession is not None:
        return _SamSession

    rembg_spec = importlib.util.find_spec("rembg")
    if rembg_spec is None or rembg_spec.origin is None:
        raise ImportError("Installed rembg package was not found.")

    sessions_dir = Path(rembg_spec.origin).resolve().parent / "sessions"
    package_name = "_room_asset_composer_rembg_sessions"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(sessions_dir)]
        sys.modules[package_name] = package

    for module_name in ("base", "sam"):
        qualified_name = f"{package_name}.{module_name}"
        if qualified_name in sys.modules:
            continue

        module_path = sessions_dir / f"{module_name}.py"
        module_spec = importlib.util.spec_from_file_location(
            qualified_name,
            module_path,
        )
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Could not load rembg SAM module: {module_path}")

        module = importlib.util.module_from_spec(module_spec)
        sys.modules[qualified_name] = module
        module_spec.loader.exec_module(module)

    _SamSession = sys.modules[f"{package_name}.sam"].SamSession
    return _SamSession


def _get_sam_session():
    global _sam_session
    if _sam_session is None:
        session_options = ort.SessionOptions()
        if "OMP_NUM_THREADS" in os.environ:
            threads = int(os.environ["OMP_NUM_THREADS"])
            session_options.inter_op_num_threads = threads
            session_options.intra_op_num_threads = threads

        _sam_session = _load_sam_session_class()("sam", session_options)
    return _sam_session


def _build_sam_prompt(points: list[tuple[int, int]]) -> list[dict]:
    return [
        {
            "type": "point",
            "label": 1,
            "data": [int(x), int(y)],
        }
        for x, y in points
    ]


def extract_by_box(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Extract the object inside a bounding box (x0, y0, x1, y1). SAM 2.1 only;
    falls back to point extraction at the box center on the v1 backend."""
    source = ImageOps.exif_transpose(image).convert("RGBA")
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))

    backend, device = _get_backend()
    if backend == "sam2.1":
        import numpy as np

        results = _sam21_model(
            source.convert("RGB"),
            bboxes=[[x0, y0, x1, y1]],
            device=device,
            verbose=False,
        )
        masks = results[0].masks
        if masks is None or masks.data.shape[0] == 0:
            return source
        union = masks.data.any(dim=0).cpu().numpy().astype(np.uint8) * 255
        mask_img = Image.fromarray(union, mode="L")
        if mask_img.size != source.size:
            mask_img = mask_img.resize(source.size, Image.NEAREST)
        empty = Image.new("RGBA", source.size, 0)
        return Image.composite(source, empty, mask_img)

    center = ((x0 + x1) // 2, (y0 + y1) // 2)
    return extract_by_click(image, [center])


def extract_by_click(image: Image.Image, points: list[tuple[int, int]]) -> Image.Image:
    """Return the clicked object mask extraction for a PIL image and point list."""
    if not points:
        raise ValueError("At least one foreground point is required.")

    source = ImageOps.exif_transpose(image).convert("RGBA")

    global _backend
    backend, device = _get_backend()
    if backend == "sam2.1":
        try:
            return _extract_sam21(source, points, device)
        except Exception as exc:
            print(f"SAM 2.1 실행 실패, v1 폴백으로 전환합니다: {exc}")
            _backend = ("rembg-sam-v1", "cpu")

    masks = _get_sam_session().predict(source, sam_prompt=_build_sam_prompt(points))
    if not masks:
        return source

    empty = Image.new("RGBA", source.size, 0)
    return Image.composite(source, empty, masks[0]).convert("RGBA")
