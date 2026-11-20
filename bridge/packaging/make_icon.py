"""アプリのアイコン(src/flatrig_bridge/assets/flatrig.ico)を作る。自作の単純な図形のみ。

使い方: python packaging/make_icon.py   (Pillow が必要: pip install -e ".[tools]")
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "flatrig_bridge" / "assets" / "flatrig.ico"
SIZE = 256


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=52, fill=(20, 110, 120, 255))
    # 平面のレイヤーが 3 枚ずれて重なる図
    for i, color in enumerate([(160, 220, 215, 255), (230, 245, 240, 255), (255, 255, 255, 255)]):
        x, y = 56 + i * 24, 156 - i * 40
        d.polygon([(x, y), (x + 120, y), (x + 96, y + 48), (x - 24, y + 48)], fill=color)
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"作成しました: {OUT}")
