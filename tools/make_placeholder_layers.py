#!/usr/bin/env python3
"""プレースホルダー素材(幾何学図形だけの顔)を生成する。

目的: 実素材の権利確認(Phase 0)を待たずに、Unity 側ツールとアニメーションの開発・検証を進める。
生成物は完全にオリジナル(単純な図形)で、既存のキャラクター・作品とは無関係。

使い方:
    python tools/make_placeholder_layers.py --out placeholder/layers
    python tools/make_placeholder_layers.py --limbs --out placeholder/limbs   # 手足の部品つきの全身(縦長)

出力:
    <out>/<name>.png   各レイヤー(不透明部分の bbox で切り抜き)
    <out>/layers.json  docs/SPEC.md §5.3 のスキーマ
    <out>/preview.png  全レイヤーを order 順に重ねた確認用画像
依存: Pillow
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

SS = 2  # スーパーサンプリング倍率(エッジを滑らかにする)

SKIN = (255, 224, 200, 255)
HAIR = (96, 72, 160, 255)
HAIR_FRONT = (112, 88, 176, 255)
CLOTH = (80, 150, 200, 255)
WHITE = (255, 255, 255, 255)
IRIS = (60, 40, 20, 255)
BROW = (70, 50, 90, 255)
MOUTH_LINE = (150, 70, 70, 255)
MOUTH_IN = (120, 30, 40, 255)

ROLES = {
    "back_hair", "body", "face", "eye_l_white", "eye_l_pupil", "eye_r_white",
    "eye_r_pupil", "brow_l", "brow_r", "mouth_closed", "mouth_open", "front_hair",
    "arm_l_upper", "arm_l_lower", "hand_l", "arm_r_upper", "arm_r_lower", "hand_r",
    "leg_l_upper", "leg_l_lower", "foot_l", "leg_r_upper", "leg_r_lower", "foot_r",
}
SLEEVE = (70, 130, 180, 255)
LEGWEAR = (60, 60, 90, 255)
SHOE = (120, 70, 50, 255)


def _s(v: float) -> float:
    return v * SS


def _ellipse(d: ImageDraw.ImageDraw, cx, cy, rx, ry, fill):
    d.ellipse([_s(cx - rx), _s(cy - ry), _s(cx + rx), _s(cy + ry)], fill=fill)


def _capsule(d: ImageDraw.ImageDraw, x0, y0, x1, y1, r, fill):
    """(x0, y0) から (x1, y1) への太さ 2r の棒(両端が丸い)。"""
    d.line([_s(x0), _s(y0), _s(x1), _s(y1)], fill=fill, width=int(_s(2 * r)))
    _ellipse(d, x0, y0, r, r, fill)
    _ellipse(d, x1, y1, r, r, fill)


def build_layers(size: int, limbs: bool = False) -> list[dict]:
    """1024 基準の座標で描き、size に合わせて拡大縮小する。limbs なら高さ 2 倍の全身で、手足の部品を足す。"""
    k = size / 1024.0
    layers: list[dict] = []
    height = size * 2 if limbs else size

    def add(name, role, group, depth, order, draw_fn):
        img = Image.new("RGBA", (size * SS, height * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        draw_fn(d, k)
        layers.append({"name": name, "role": role, "group": group,
                       "depth": depth, "order": order, "image": img})

    add("back_hair", "back_hair", "head", -0.6, 0,
        lambda d, k: _ellipse(d, 512 * k, 470 * k, 330 * k, 380 * k, HAIR))

    def body(d, k):
        bottom = 1330 if limbs else 1040
        d.rounded_rectangle([_s(210 * k), _s(760 * k), _s(814 * k), _s(bottom * k)],
                            radius=_s(90 * k), fill=CLOTH)
    add("body", "body", "body", 0.0, 1, body)

    if limbs:
        # 画面左(_l)の手足は x が小さい側。脚は体より奥、腕は体より手前(顔・髪より奥)に置く
        for side, sx in (("l", -1), ("r", 1)):
            hx = 512 + sx * 110
            add(f"leg_{side}_upper", f"leg_{side}_upper", "body", 0.0, 100 + (sx > 0),
                lambda d, k, hx=hx: _capsule(d, hx * k, 1290 * k, hx * k, 1620 * k, 70 * k, LEGWEAR))
            add(f"leg_{side}_lower", f"leg_{side}_lower", "body", 0.0, 102 + (sx > 0),
                lambda d, k, hx=hx: _capsule(d, hx * k, 1620 * k, hx * k, 1910 * k, 55 * k, LEGWEAR))
            add(f"foot_{side}", f"foot_{side}", "body", 0.0, 104 + (sx > 0),
                lambda d, k, hx=hx, sx=sx: _capsule(d, hx * k, 1915 * k, (hx + sx * 60) * k, 1975 * k, 40 * k, SHOE))
            ax = 512 + sx * 330
            add(f"arm_{side}_upper", f"arm_{side}_upper", "body", 0.0, 110 + (sx > 0),
                lambda d, k, ax=ax, sx=sx: _capsule(d, ax * k, 820 * k, (ax + sx * 40) * k, 1070 * k, 55 * k, SLEEVE))
            add(f"arm_{side}_lower", f"arm_{side}_lower", "body", 0.0, 112 + (sx > 0),
                lambda d, k, ax=ax, sx=sx: _capsule(d, (ax + sx * 40) * k, 1070 * k, (ax + sx * 60) * k, 1300 * k, 45 * k, SKIN))
            add(f"hand_{side}", f"hand_{side}", "body", 0.0, 114 + (sx > 0),
                lambda d, k, ax=ax, sx=sx: _capsule(d, (ax + sx * 60) * k, 1310 * k, (ax + sx * 65) * k, 1380 * k, 45 * k, SKIN))

    add("face", "face", "head", 0.2, 2,
        lambda d, k: _ellipse(d, 512 * k, 450 * k, 250 * k, 290 * k, SKIN))

    # order はレイヤーごとに一意(同一メッシュ内の描画順を確定させるため。SPEC §5.3)
    for i, (side, cx) in enumerate((("l", 410), ("r", 614))):
        add(f"eye_{side}_white", f"eye_{side}_white", "head", 0.35, 3 + 2 * i,
            lambda d, k, cx=cx: _ellipse(d, cx * k, 470 * k, 62 * k, 44 * k, WHITE))
        add(f"eye_{side}_pupil", f"eye_{side}_pupil", "head", 0.4, 4 + 2 * i,
            lambda d, k, cx=cx: _ellipse(d, cx * k, 470 * k, 24 * k, 24 * k, IRIS))

    for i, (side, cx) in enumerate((("l", 410), ("r", 614))):
        def brow(d, k, cx=cx):
            d.rounded_rectangle([_s((cx - 58) * k), _s(392 * k), _s((cx + 58) * k), _s(408 * k)],
                                radius=_s(8 * k), fill=BROW)
        add(f"brow_{side}", f"brow_{side}", "head", 0.4, 7 + i, brow)

    def mouth_closed(d, k):
        d.rounded_rectangle([_s(462 * k), _s(618 * k), _s(562 * k), _s(626 * k)],
                            radius=_s(4 * k), fill=MOUTH_LINE)
    add("mouth_closed", "mouth_closed", "head", 0.4, 9, mouth_closed)

    add("mouth_open", "mouth_open", "head", 0.41, 10,
        lambda d, k: _ellipse(d, 512 * k, 628 * k, 38 * k, 34 * k, MOUTH_IN))

    def front_hair(d, k):
        pts = [(250, 330), (300, 190), (512, 140), (724, 190), (774, 330),
               (700, 300), (620, 360), (540, 300), (470, 370), (400, 300), (320, 360)]
        d.polygon([(_s(x * k), _s(y * k)) for x, y in pts], fill=HAIR_FRONT)
    add("front_hair", "front_hair", "head", 0.7, 11, front_hair)

    return layers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="placeholder/layers", help="出力ディレクトリ")
    parser.add_argument("--size", type=int, default=1024, help="キャンバスの幅(px)。高さは同じ(--limbs なら 2 倍)")
    parser.add_argument("--limbs", action="store_true", help="手足の部品(腕・脚を関節で分けたもの)つきの全身にする")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    size = args.size

    layers = build_layers(size, args.limbs)
    height = size * 2 if args.limbs else size
    orders = [layer["order"] for layer in layers]
    if len(set(orders)) != len(orders):
        raise RuntimeError("order が重複しています(各レイヤーで一意にしてください)")
    preview = Image.new("RGBA", (size, height), (235, 240, 245, 255))
    entries = []

    for layer in sorted(layers, key=lambda x: x["order"]):
        assert layer["role"] in ROLES, layer["role"]
        full = layer["image"].resize((size, height), Image.LANCZOS)
        bbox = full.getbbox()
        if bbox is None:
            raise RuntimeError(f"レイヤー {layer['name']} が空です")
        cropped = full.crop(bbox)
        cropped.save(out / f"{layer['name']}.png")
        preview.alpha_composite(full)
        x0, y0, x1, y1 = bbox
        entries.append({
            "name": layer["name"],
            "role": layer["role"],
            "group": layer["group"],
            "file": f"{layer['name']}.png",
            "bbox": [x0, y0, x1, y1],
            "pivot": [(x0 + x1) / 2, (y0 + y1) / 2],
            "depth": layer["depth"],
            "order": layer["order"],
        })

    (out / "layers.json").write_text(
        json.dumps({"canvas": [size, height], "layers": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    preview.save(out / "preview.png")
    print(f"{len(entries)} レイヤーを {out} に生成しました(layers.json, preview.png を含む)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
