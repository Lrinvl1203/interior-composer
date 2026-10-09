"""Build original illustrative swatches; never import personal/supplier images."""
from pathlib import Path
import json
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "webapp" / "data" / "finish_library"
CATEGORIES = [("wallpaper", "도배"), ("flooring", "장판"), ("film", "필름"), ("tile", "타일")]
SAMPLES = [
    ("wp-pure-white", "wallpaper", "순백 무지 · 예시", "화이트", "무광", "#f4f2eb", "plain"),
    ("wp-sage-green", "wallpaper", "세이지그린 · 예시", "그린", "무광", "#a3af98", "plain"),
    ("wp-warm-gray", "wallpaper", "웜그레이 · 예시", "그레이", "무광", "#c1bdb2", "plain"),
    ("fl-light-oak", "flooring", "라이트 오크 · 예시", "우드", "무광", "#c8a67b", "wood"),
    ("fl-walnut", "flooring", "월넛 플랭크 · 예시", "우드", "무광", "#795b43", "wood"),
    ("fl-gray-stone", "flooring", "그레이 스톤 · 예시", "그레이", "무광", "#a9aaa5", "stone"),
    ("fm-warm-white", "film", "웜화이트 필름 · 예시", "화이트", "무광", "#e9e3d6", "plain"),
    ("fm-oak", "film", "오크 필름 · 예시", "우드", "무광", "#b88e63", "wood"),
    ("fm-charcoal", "film", "차콜 필름 · 예시", "차콜", "무광", "#494b47", "plain"),
    ("tl-ivory", "tile", "아이보리 타일 · 예시", "아이보리", "무광", "#e5dfd1", "tile"),
    ("tl-sage", "tile", "세이지 타일 · 예시", "그린", "무광", "#9fae98", "tile"),
    ("tl-gray", "tile", "그레이 타일 · 예시", "그레이", "무광", "#b7b8b2", "tile"),
]

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for item_id, category, name, tone, finish, color, pattern in SAMPLES:
        directory = ROOT / category
        directory.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (320, 180), color)
        draw = ImageDraw.Draw(image)
        if pattern == "wood":
            for y in range(0, 180, 36):
                draw.line((0, y, 320, y), fill="#78664f", width=2)
                for x in range((y // 36 % 2) * 75, 320, 150):
                    draw.line((x, y, x, y + 36), fill="#78664f")
                for grain in range(y + 8, y + 32, 8):
                    draw.line((0, grain, 320, grain), fill="#ab8966")
        elif pattern == "tile":
            for x in range(0, 320, 80):
                draw.line((x, 0, x, 180), fill="#eeeae1", width=3)
            for y in range(0, 180, 60):
                draw.line((0, y, 320, y), fill="#eeeae1", width=3)
        elif pattern == "stone":
            for x in range(0, 320, 17):
                draw.line((x, 0, x + 65, 180), fill="#b9bab6")
        image.save(directory / (item_id + ".png"))
        items.append({"id": item_id, "category": category, "name": name, "tone": tone,
                      "finish": finish, "tier": "예시", "note": "절차적으로 만든 설명용 샘플. 실제 제품·성능 보증이 아닙니다."})
    catalog = {"version": "standalone-samples-1", "categories": [{"key": key, "label": label} for key, label in CATEGORIES], "items": items}
    (ROOT / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Created {len(items)} original sample swatches.")

if __name__ == "__main__":
    main()
