#!/usr/bin/env python3
"""PSD から FlatRig のレイヤー素材(PNG 群と layers.json)を書き出す。人間の PC で実行する。

実素材の画像は Claude に渡さない運用のため(docs/MATERIAL_DECISION.md)、このツールは
文字情報(レイヤー名・位置)だけを表示し、画像はローカルの出力フォルダにだけ書く。
絵の色や形は変えない。行うのは、選んだレイヤーの重ね合わせ・切り抜き・縮小と、
透明部分(見えない画素)の色埋めだけ。

使い方(bridge の .venv で。初回は  pip install -e ".[tools]"  で psd-tools を入れる):
    0) 1 回で書き出す。部品分けは自動
       (命名規則の名前 → 名前からの推測 → 位置と重なり順。auto_mapping を参照)
       python tools/psd_to_layers.py auto <PSD> [--out <フォルダ>]   (既定: PSD と同じ場所の <PSD名>_FlatRig)
       → <out>/mapping.json(自動)・<out>/psd_layers.txt・<out>/layers/。<out>/mapping.json が既にあればそれを使う
       命名規則: レイヤー/グループ名を役割名(face, eye_l_white …)にする。詳しくは docs/AVATAR_GUIDE.md
       python tools/psd_to_layers.py edit <PSD>   修正画面(tools/psd_editor.py)で確認・修正してから書き出す
       (配布版の FlatRigPSD.exe に PSD をドロップすると、こちらが開く)
    1) レイヤー一覧と対応表(自動)を作る
       python tools/psd_to_layers.py inspect <PSD> [--out <フォルダ>]   (既定: リポジトリの art/private/<PSD名>)
       → <out>/psd_layers.txt(一覧)と <out>/mapping.json(対応表の下書き)
    2) mapping.json を確認・修正する(どの PSD レイヤーを FlatRig のどのレイヤーにまとめるか)
    3) 書き出す
       python tools/psd_to_layers.py export <out>/mapping.json
       → <out>/layers/ に PNG・layers.json・preview.png

mapping.json:
    {
      "psd": "C:/.../素材.psd",
      "scale": 0.5,                      # 縮小率。1/整数(1, 0.5, 0.25 …)。4000px → 2000px
      "head_pivot": [x, y],              # 任意。PSD の座標(px)。首の付け根
      "body_pivot": [x, y],              # 任意
      "layers": [                        # 奥 → 手前の順。この順が order になる
        { "name": "back_hair", "role": "back_hair", "group": "head", "depth": -0.6,
          "sources": ["後ろ髪/*"] }        # PSD のレイヤーのパス(グループ/名前)。* ? が使える
      ],
      "mouth_frames": true,              # 任意。口は閉じ・半開き・開きの絵の差し替え(mouth_closed / mouth_half / mouth_open)
      "expressions": [                   # 任意。表情(最大 MAX_EXPRESSIONS 個)。番号は 1 から並び順
        { "name": "笑顔", "parts": { "eye_l": ["左目/閉じた笑顔左まぶた"], "mouth_open": ["..."] } }
      ],                                 # parts の枠は EXPR_SLOTS。選ばなかった枠は通常の部品を使う
      "unused": ["..."]                  # どこにも入れていないレイヤー(確認用。書き出しでは無視)
    }
- sources に一致したレイヤーは PSD の重ね順で合成する。非表示のレイヤーは、
  "include_hidden": true を付けたときだけ使う(表情差分などを誤って入れないため)。
- クリッピングされたレイヤーは、元のレイヤーを選べば一緒に合成される。
- 口の隠し(「上唇隠し」など)がある PSD では、口内・歯・舌以外の口のレイヤーを閉じた口にまとめ、
  mouth_closed に "hide_when_open": true を付ける(口を開くと閉じた口を消し、口の中を唇の線の位置から開く)。
  開いた口には "open_from_width"(開き始めの横幅の比 = 唇の線の幅 / 口の中の幅)を付ける。
- 描画モードは 通常/乗算/スクリーン/覆い焼き(リニア)/オーバーレイ に対応。その他は通常として扱い、警告を出す。
- 非表示のレイヤーを部品にしたとき(閉じた目・口の差し替えの絵や表情の部品)は "include_hidden": true を付ける。
- 表情(docs/SPEC.md §5.9): 表情ごとに、左目・右目・口(閉じ/半開き/開き)・追加パーツに使うレイヤーを選ぶ。
  書き出しでは表情の部品を role "expr_<枠>" と "expr"(表情の番号)を持つレイヤーにし、入れ替える通常の部品の直後に重ねる。
  layers.json には "expressions"(表情の名前の並び)と "mouth_frames" を書く。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ROLES = (
    "back_hair", "body", "face", "eye_l_white", "eye_l_pupil", "eye_r_white",
    "eye_r_pupil", "brow_l", "brow_r", "mouth_closed", "mouth_open", "front_hair",
    "eye_l_lash", "eye_r_lash", "eye_l_shadow", "eye_r_shadow", "eye_l_highlight", "eye_r_highlight",
    "eye_l_closed", "eye_r_closed", "mouth_half",
    *(f"{limb}_{side}{part}" for limb, parts in (("arm", ("_upper", "_lower")), ("leg", ("_upper", "_lower")))
      for side in ("l", "r") for part in parts),
    "hand_l", "hand_r", "foot_l", "foot_r",
    "nose",
)
# 手足(docs/SPEC.md §5.10)。Unity の「手足をトラッキングに合わせる」が ON のとき、関節で曲がる。OFF なら体と同じ
LIMB_PREFIXES = ("arm_", "leg_", "hand_", "foot_")
LIMB_ROLES = tuple(r for r in ROLES if r.startswith(LIMB_PREFIXES))
FUNCTIONAL_ROLES = {r for r in ROLES if r.startswith(("eye_", "brow_", "mouth_"))} | {"nose"}
# 表情(docs/SPEC.md §5.9)。枠ごとに通常の部品と入れ替える。番号 0 は通常の表情
EXPR_SLOTS = ("eye_l", "eye_r", "mouth_closed", "mouth_half", "mouth_open", "extra")
EXPR_SLOT_LABELS = {
    "eye_l": "左目", "eye_r": "右目", "mouth_closed": "閉じた口", "mouth_half": "半開きの口",
    "mouth_open": "開いた口", "extra": "追加パーツ(頬染めなど)",
}
EXPR_SLOT_DEPTH = {"eye_l": 0.22, "eye_r": 0.22, "mouth_closed": 0.16, "mouth_half": 0.155, "mouth_open": 0.15,
                   "extra": 0.05}
MAX_EXPRESSIONS = 15  # Unity のシェーダで扱える数(通常を含めて 16)
DEFAULT_SCALE = 0.5
EDGE_MARGIN = 0  # 書き出し後の px。パーツの周りに残す透明の余白(0 = 余白なし)
PRIVATE_DIR = Path(__file__).resolve().parents[1] / "art" / "private"  # gitignore 済み


# --- PSD の読み込み -----------------------------------------------------------------


@dataclass
class Leaf:
    path: str          # グループ/…/名前
    layer: object      # psd_tools のレイヤー
    visible: bool      # 自分と親グループがすべて表示
    index: int         # 奥からの順番
    parents: tuple[str, ...] = ()  # 親グループの名前(外側 → 内側)


def open_psd(path: Path):
    try:
        from psd_tools import PSDImage
    except ImportError:
        sys.exit('psd-tools がありません。bridge で  .venv\\Scripts\\python.exe -m pip install -e ".[tools]"  を実行')
    return PSDImage.open(path)


def leaves(psd) -> list[Leaf]:
    """画像を持つレイヤーを奥から手前の順に並べる(グループは展開する)。"""
    out: list[Leaf] = []

    def walk(container, parents: tuple[str, ...], parent_visible: bool) -> None:
        for layer in container:  # psd-tools は奥(下)から順に返す
            path = "/".join((*parents, layer.name))
            visible = parent_visible and bool(layer.visible)
            if layer.is_group():
                walk(layer, (*parents, layer.name), visible)
            else:
                out.append(Leaf(path, layer, visible, len(out), parents))

    walk(psd, (), True)
    return out


def blend_name(layer) -> str:
    mode = getattr(layer, "blend_mode", None)
    return str(getattr(mode, "name", mode) or "NORMAL").upper()


def is_clipping(layer) -> bool:
    value = getattr(layer, "clipping", None)  # psd-tools 1.10 以降
    return value if isinstance(value, bool) else bool(getattr(layer, "clipping_layer", False))


# --- inspect ------------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    psd_path = Path(args.psd).resolve()
    psd = open_psd(psd_path)
    out = Path(args.out).resolve() if args.out else PRIVATE_DIR / psd_path.stem
    out.mkdir(parents=True, exist_ok=True)
    items = leaves(psd)

    text = layer_table(psd_path, psd, items)
    (out / "psd_layers.txt").write_text(text, encoding="utf-8")
    print(text)

    mapping_path = out / "mapping.json"
    if mapping_path.exists() and not args.force:
        print(f"{mapping_path} は既にあるので上書きしません(作り直すときは --force)")
        return 0
    m, _ = auto_mapping(psd_path, psd, items)
    mapping_path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"一覧: {out / 'psd_layers.txt'}")
    print(f"対応表(自動): {mapping_path}(内容を確認・修正してから export してください)")
    return 0


def layer_table(psd_path: Path, psd, items: list[Leaf]) -> str:
    lines = [f"PSD: {psd_path.name}  {psd.width}x{psd.height}px  レイヤー {len(items)} 枚(奥 → 手前)", ""]
    header = f"{'#':>3}  {'表示':<4} {'不透明度':>4}  {'描画モード':<14} {'クリップ':<4} {'位置 (x0,y0)-(x1,y1)':<28}"
    lines.append(header + " パス")
    for leaf in items:
        bbox = tuple(leaf.layer.bbox)
        lines.append(
            f"{leaf.index:>3}  {'○' if leaf.visible else '-':<4} {leaf.layer.opacity:>4}  {blend_name(leaf.layer):<14} "
            f"{'○' if is_clipping(leaf.layer) else '':<4} {str(bbox):<28} {leaf.path}"
        )
    return "\n".join(lines) + "\n"


# --- 自動の対応表(docs/AVATAR_GUIDE.md) -------------------------------------------
# 1. 命名規則の名前(自分か親グループ)があればそれに従う
# 2. 無ければ名前から推測する(日本語と英語)
# 3. 名前で分からなければ、位置(顔の範囲か)と重なり順(顔より奥か手前か)で決める
# 表示されているレイヤーは、差分・ガイドと判断したもの以外すべて使う(透かしなども消さない)。

ROLE_DEFAULTS: dict[str, tuple[str, float]] = {  # 役割 → (group, depth)
    "back_hair": ("head", -0.6), "body": ("body", 0.0), "face": ("head", 0.0),
    "eye_l_white": ("head", 0.2), "eye_r_white": ("head", 0.2),
    "eye_l_pupil": ("head", 0.25), "eye_r_pupil": ("head", 0.25),
    "eye_l_lash": ("head", 0.22), "eye_r_lash": ("head", 0.22),
    "eye_l_shadow": ("head", 0.21), "eye_r_shadow": ("head", 0.21),
    "eye_l_highlight": ("head", 0.26), "eye_r_highlight": ("head", 0.26),
    "brow_l": ("head", 0.3), "brow_r": ("head", 0.3),
    "mouth_closed": ("head", 0.16), "mouth_open": ("head", 0.15), "front_hair": ("head", 0.6),
    "eye_l_closed": ("head", 0.22), "eye_r_closed": ("head", 0.22), "mouth_half": ("head", 0.155),
    "nose": ("head", 0.25),  # 鼻(影・ハイライトも含めて 1 つ)。目と眉の間くらいの奥行きで顔の向きに付いて動く
    **{r: ("body", 0.0) for r in LIMB_ROLES},
}
ROLE_LABELS = {
    "back_hair": "後ろ髪", "body": "体", "face": "顔", "front_hair": "前髪",
    "eye_l_white": "白目(左)", "eye_r_white": "白目(右)",
    "eye_l_pupil": "黒目(左)", "eye_r_pupil": "黒目(右)",
    "eye_l_lash": "まつ毛(左)", "eye_r_lash": "まつ毛(右)",
    "eye_l_shadow": "白目の影(左)", "eye_r_shadow": "白目の影(右)",
    "eye_l_highlight": "瞳のハイライト(左)", "eye_r_highlight": "瞳のハイライト(右)",
    "brow_l": "眉(左)", "brow_r": "眉(右)", "mouth_closed": "閉じた口", "mouth_open": "開いた口",
    "eye_l_closed": "閉じた目の絵(左・まばたき)", "eye_r_closed": "閉じた目の絵(右・まばたき)",
    "mouth_half": "半開きの口(絵の差し替え)", "nose": "鼻",
    "arm_l_upper": "上腕(左)", "arm_l_lower": "前腕(左)", "hand_l": "手(左)",
    "arm_r_upper": "上腕(右)", "arm_r_lower": "前腕(右)", "hand_r": "手(右)",
    "leg_l_upper": "太もも(左)", "leg_l_lower": "すね(左)", "foot_l": "足(左)",
    "leg_r_upper": "太もも(右)", "leg_r_lower": "すね(右)", "foot_r": "足(右)",
}
RECOMMENDED_ROLES = ("face", "eye_l_white", "eye_l_pupil", "eye_r_white", "eye_r_pupil", "mouth_closed", "mouth_open")
NAME_SEPARATORS = "._- "
DEPTH_SUFFIX = re.compile(r"^(.*?)\s*@\s*([+-]?\d+(?:\.\d+)?)$")
MAX_AUTO_HEIGHT = 3000  # 自動の縮小率: 書き出し後の高さがこれ以下になる 1/整数 を選ぶ
SIDE_HAIR_DEPTH = 0.3
FACE_ACCESSORY_DEPTH = 0.1  # 顔より手前にある、名前で分からない頭の部品(アクセサリーなど)
# 名前で分からないレイヤーが目・口の範囲に VARIANT_INSIDE 以上入っていて、目・口の範囲の VARIANT_AREA 以上の大きさか、
# 名前が「レイヤー 1」のような付けたままの名前なら、表情差分とみなす(小さな飾りは使う)
VARIANT_INSIDE = 0.8
VARIANT_AREA = 0.2
DEFAULT_LAYER_NAME = re.compile(r"^(レイヤー|layer|图层|圖層)\s*\d+$", re.I)

# 口の「隠し」: 口内を最大まで開いた形で描き、肌色の隠しレイヤーと唇の線で閉じた口を作る構造。
# 隠しがあれば、口の中(MOUTH_INSIDE)以外の口のレイヤーはすべて閉じた口にまとめ、
# mapping/layers.json の mouth_closed に "hide_when_open": true を付ける(Unity では口を開くと閉じた口を消す)
MOUTH_COVER = r"(口|唇).*(隠し|かくし)|(lip|mouth).?(hide|cover)"
MOUTH_PAIRS = {"mouth_open": "mouth_closed"}  # 開いた口 → 閉じた口
MOUTH_INSIDE = re.compile(r"口内|口の中|歯|舌|mouth.?in|teeth|tooth|tongue", re.I)
# 名前からの推測。レイヤー名 → 親グループ名(近い順)の順に見て、最初に合った規則を使う。
# 規則は (パターン, 種類, 条件)。種類の ? は左右(位置で決める)。条件:
#   None      いつでも
#   "eye"     近い名前に目の言葉(EYE_CONTEXT)があるときだけ(「hi」「反射光」「white」など、目以外にも使う言葉)
#   "eye_soft" 近い名前に目以外の言葉(NON_EYE: 鼻・頬・唇・髪 …)があるときは使わない(「ハイライト」)
#   "hair"    名前か親グループに髪の言葉があるときだけ(「サイド」)
# 種類 "hair" は顔より奥なら後ろ髪・手前なら前髪、"side_hair" は奥なら後ろ髪・手前なら横髪(前髪の奥行き 0.3)。
# 手足の "arm_?" "leg_?"(部位の分からない腕・脚・袖)は、同じ側の上腕・前腕(太もも・すね)のうち重なりの大きい方、
# 無ければ上腕・太もも。"arm_?_joint" "leg_?_joint"(肘・膝)は上腕・太ももの中に収まっていればそちら、はみ出していれば前腕・すね。


def _en(word: str) -> str:
    """英語の単語(前後が英字でない。left/right/l/r の前置き、s/l/r の後置きは可)。"""
    return rf"(?:(?<![a-z])|(?<=left)|(?<=right)|(?<=[_\-\s.][lr]))(?:{word})(?:s|l|r)?(?![a-z])"


EYE_CONTEXT = re.compile(r"目|眼|瞳|まぶた|瞼|eye|iris|pupil", re.I)
NON_EYE = re.compile(r"鼻|頬|ほほ|ほっぺ|チーク|唇|口|リップ|髪|nose|cheek|lip|mouth|hair", re.I)
HAIR_WORD = re.compile(r"髪|毛|hair|bang", re.I)
GUESS_RULES: list[tuple[str, str, str | None]] = [
    # 髪(帽子などの頭にかぶるものも、髪と同じく顔より奥か手前かで決める)
    (r"後ろ髪|後髪|うしろ髪|後頭部|back.?hair|hair.?back|rear.?hair|后发|後髮", "back_hair", None),
    (r"前髪|front.?hair|hair.?front|bangs?(?![a-z])|fringe|刘海|前发|前髮", "front_hair", None),
    (r"横髪|もみあげ|揉み上げ|side.?hair|hair.?side|sideburn|侧发|側髮", "side_hair", None),
    (r"サイド|" + _en("side"), "side_hair", "hair"),
    ((r"髪|毛先|襟足|アホ毛|あほ毛|ハネ毛|はね毛|おさげ|三つ編み|みつあみ|ポニーテール|ポニテ|ツインテ|お団子|"
      r"hair|ponytail|twin.?tail|braid|头发|頭髮"), "hair", None),
    (r"帽子|帽|ハット|キャップ|ベレー|" + _en("hat|cap|beret|hood|helmet"), "hair", None),
    # 目・眉
    (r"目.*(影|かげ|シャドウ)|eye.?(white.?)?shadow", "eye_?_shadow", None),  # 白目の上部の影(まつ毛と一緒に下がる)
    (r"白目|眼白|eye.?white|white.?eye|sclera", "eye_?_white", None),
    (_en("white"), "eye_?_white", "eye"),
    # 瞳のハイライト: 黒目と一緒に動き、まぶたが下りてくると押されて少し下がる(鼻・頬・唇・髪のハイライトは対象外)
    (r"(?<![鼻頬唇口])(ハイライト|highlight)", "eye_?_highlight", "eye_soft"),
    (r"反射|キャッチライト|catch.?light|(?<![a-z])(hi|hl)(?:[lr])?(?![a-z])", "eye_?_highlight", "eye"),  # 瞳hi1 など
    (r"瞳|黒目|目玉|虹彩|眼球|眼珠|iris|pupil", "eye_?_pupil", None),
    (r"まつ毛|まつげ|睫毛|睫|二重|まぶた|瞼|アイライン|lash|eyelid|eye.?line|眼皮", "eye_?_lash", None),
    (r"眉|まゆ|brow", "brow_?", None),
    # 口
    (MOUTH_COVER, "mouth_closed", None),  # 「上唇隠し」など: 閉じた口の肌で口内を覆う作り(MOUTH_COVER を参照)
    (r"口内|口の中|口腔|歯|舌|牙|下唇|mouth.?(in|open)|open.?mouth|teeth|tooth|tongue|fang|lower.?lip|lip.?lower",
     "mouth_open", None),  # 開くと見える
    (r"口|唇|リップ|mouth|(?<![a-z])lip|upper.?lip|嘴", "mouth_closed", None),
    # 鼻(影・ハイライトも鼻。「鼻ハイライト」「鼻の影」「nose shadow」)
    (r"鼻|(?<![ぁ-ゖ])はな(?:の|(?![ぁ-ゖ]))|(?<![ァ-ヶー])ハナ(?![ァ-ヶー])|nose|nostril|鼻子", "nose", None),
    # 顔
    (r"顔|輪郭|耳|頬|ほほ|ほっぺ|チーク|フェイス|おでこ|額|あご|顎|face|(?<![a-z])ear|cheek|blush|脸|臉|" + _en("head"),
     "face", None),
    # 手足: 部品に分かれた腕・脚(左右は位置で決める)。腕・脚が 1 枚なら上腕・太ももになり、肩・股関節で回る
    (r"上腕|二の腕|upper.?arm|arm.?upper|上臂|大臂", "arm_?_upper", None),
    (r"前腕|下腕|fore.?arm|lower.?arm|arm.?lower|前臂|小臂", "arm_?_lower", None),
    (r"肘|ひじ|elbow", "arm_?_joint", None),
    (r"すね|脛|ふくらはぎ|下腿|小腿|shin|calf|calves|lower.?leg|leg.?lower", "leg_?_lower", None),
    (r"太もも|太腿|ふともも|大腿|もも|腿|thigh|upper.?leg|leg.?upper", "leg_?_upper", None),
    (r"ひざ|膝|knee", "leg_?_joint", None),
    (r"手(?!前)|指|拳|掌|てのひら|hand|finger|thumb|fist|glove|palm|手套", "hand_?", None),
    (r"靴|足先|つま先|爪先|足首|かかと|踵|foot|feet|shoe|boot|sock|sandal|heel|鞋", "foot_?", None),
    (r"腕|袖|そで|sleeve|手臂|胳膊|" + _en("arm"), "arm_?", None),
    (r"脚|足|ニーソ|ニーハイ|タイツ|ストッキング|stocking|tights|" + _en("leg"), "leg_?", None),
    # 体
    ((r"体|胴|ボディ|服|衣装|首|胸|腹|肩|腰|背中|鎖骨|デコルテ|谷間|襟|えり|シャツ|ブラウス|ドレス|ワンピ|スカート|"
      r"ズボン|パンツ|ベルト|ネクタイ|上着|ジャケット|コート|ベスト|パーカー|股|裾|しっぽ|尻尾|"
      r"body|torso|chest|neck|skirt|cloth|shirt|dress|collar|pants|belt|jacket|coat|tail|身体|身體|衣服"), "body", None),
    (r"肌|skin", "face", None),  # 腕の肌などは上の規則が先
]
# 表情の自動の振り分け(auto_expressions)
SMILE_NAME = re.compile(r"笑顔|笑い|にっこり|にこにこ|ニコニコ|スマイル|smile|happy", re.IGNORECASE)
EXPR_GROUP = re.compile(r"^\s*(?:expr|表情)\s*[:：.]\s*(.+?)\s*$", re.IGNORECASE)  # 「表情:怒り」グループ(命名規則)
LEGACY_SMILE = {"eye_l_smile": "eye_l", "eye_r_smile": "eye_r", "mouth_smile": "mouth_closed",
                "mouth_smile_open": "mouth_open", "smile_extra": "extra"}  # 前の版の笑顔の役割名
MOUTH_WORD = re.compile(r"口|唇|mouth|lip", re.IGNORECASE)
FRAME_WORDS = (("mouth_half", re.compile(r"半開|半びらき|half", re.IGNORECASE)),
               ("mouth_open", re.compile(r"開|ひらき|あけ|open", re.IGNORECASE)),
               ("mouth_closed", re.compile(r"閉|とじ|close|shut", re.IGNORECASE)))
EYE_WORD = re.compile(r"まぶた|瞼|目|眼|eye|lid", re.IGNORECASE)
CLOSED_WORD = re.compile(r"閉|とじ|つむ|close|shut", re.IGNORECASE)
LABEL_NOISE = re.compile(
    r"半開き?|半びらき|開[きけ]?|ひらき|あけ|閉じた|閉じ|閉|とじ|つむり?|"
    r"まぶた|瞼|目|眼|口|唇|左|右|の|"
    r"half|open|closed?|shut|mouth|lips?|eyes?|lids?|left|right|[_\-\s.]", re.IGNORECASE)
NOT_DEFAULT = re.compile(
    r"差分|照れ|青ざめ|ハート目|キラキラ|ぐるぐる|ガイド|マスク|閉じ目|閉眼|笑い目|ウィンク|ウインク|汗|涙|"
    r"guide|mask|diff|wink", re.I)
# 下書き・見本(表情の自動の振り分けでも使わない)と背景
ROUGH = re.compile(r"(?<![ァ-ヶー])ラフ|下書き|見本|目安|参考|アタリ|sketch|rough|reference|(?<![a-z])ref(?![a-z])", re.I)
BACKGROUND = re.compile(r"背景|background|(?<![a-z])bg(?![a-z])|^(用紙|紙|paper)\s*\d*$", re.I)
BACKGROUND_COVER = 0.98  # 一番奥の名前の分からないレイヤーがキャンバスのこれ以上を覆っていれば背景
FACE_OUTLINE = re.compile(r"顔|輪郭|肌|face|skin", re.I)  # 首の付け根(head_pivot)を決める顔のレイヤー


def _context(names: tuple[str, ...]) -> str | None:
    """近い名前から見て、目の言葉が先なら "eye"、目以外の顔の言葉(鼻・唇・髪 …)が先なら "other"。"""
    for n in names:
        if NON_EYE.search(n):
            return "other"
        if EYE_CONTEXT.search(n):
            return "eye"
    return None


def guess_with_level(names: tuple[str, ...]) -> tuple[str | None, int]:
    """names は内側(レイヤー名)→ 外側(親グループ)の順。戻り値は (種類, 合った名前の位置。0 = レイヤー名)。"""
    for level, name in enumerate(names):
        for pattern, kind, cond in GUESS_RULES:
            if not re.search(pattern, name, re.I):
                continue
            if cond == "eye" and _context(names) != "eye":
                continue
            if cond == "eye_soft" and _context(names) == "other":
                continue
            if cond == "hair" and not any(HAIR_WORD.search(n) for n in names):
                continue
            return kind, level
    return None, -1


def guess(names: tuple[str, ...]) -> str | None:
    """names は内側(レイヤー名)→ 外側(親グループ)の順。"""
    return guess_with_level(names)[0]


def _role_key(name: str) -> str:
    """役割名の比べ方: 大文字小文字・区切り(空白・-)・キャメルケースの違いを無くす(BackHair → back_hair)。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return re.sub(r"[\s\-]+", "_", s).lower()


def parse_part_name(name: str) -> tuple[str, str, float | None] | None:
    """「役割名」「役割名.任意名」「…@奥行き」を (役割, 部品名, 奥行き) にする。合わなければ None。"""
    base, depth = name.strip(), None
    m = DEPTH_SUFFIX.match(base)
    if m:
        base, depth = m.group(1).strip(), float(m.group(2))
    low = _role_key(base) if base.isascii() else base.lower()
    for role in sorted(ROLES, key=len, reverse=True):  # eye_l_white を eye_l より先に見る
        if low == role or (low.startswith(role) and low[len(role)] in NAME_SEPARATORS):
            return role, base, depth
    return None


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _glob_escape(path: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", path)


Box = tuple[int, int, int, int]


def _box(leaf: Leaf) -> Box:
    return tuple(leaf.layer.bbox)


def _union(boxes) -> Box | None:
    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
    if not boxes:
        return None
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def _inside(box: Box, region: Box | None) -> float:
    """box のうち region に入っている面積の割合。"""
    area = (box[2] - box[0]) * (box[3] - box[1])
    if region is None or area <= 0:
        return 0.0
    w = min(box[2], region[2]) - max(box[0], region[0])
    h = min(box[3], region[3]) - max(box[1], region[1])
    return max(0, w) * max(0, h) / area


@dataclass
class Pick:
    """レイヤー 1 枚の振り分け。role が None なら使わない(how に理由)。"""

    leaf: Leaf
    role: str | None
    depth: float = 0.0
    explicit: bool = False  # 名前の @奥行き で指定された
    part: str | None = None  # 部品名を固定する(命名規則・既存の対応表)。機能部品と自動の部品は None
    how: str = ""  # 判断の根拠(修正画面と一覧に出す)
    quiet: bool = False  # 使わないことを一覧に出さない(_ や # で始まる下書き)


CANCELLED_MESSAGE = "書き出さずに閉じました。"  # 修正画面を書き出さずに閉じた(エラーではない)
HOW_NAMED, HOW_NAME, HOW_POSITION, HOW_MANUAL, HOW_MAPPING = "役割名", "名前から", "位置から", "手動", "対応表"


def _left_share(leaf: Leaf, cx: float) -> float | None:
    """レイヤーの不透明な画素のうち、x < cx にあるものの割合。画素を読めなければ None。"""
    topil = getattr(leaf.layer, "topil", None)
    if topil is None:
        return None
    try:
        img = topil()
    except Exception:  # noqa: BLE001  画素の読めないレイヤー(調整レイヤーなど)
        return None
    if img is None or "A" not in img.getbands():
        return None
    cols = np.asarray(img.getchannel("A"), dtype=np.float64).sum(axis=0)
    total = cols.sum()
    if total <= 0:
        return None
    split = round(cx - leaf.layer.bbox[0])
    return float(cols[:max(0, min(len(cols), split))].sum() / total)


SIDE_SHARE = 0.8  # 左右にまたがる手足のレイヤーは、片側にこれ以上の画素があればその側(はみ出した影・線などを許す)


def _side(leaf: Leaf, cx: float) -> str | None:
    """画面の左なら "l"、右なら "r"。手足のレイヤーが両側にまたがっていれば(両腕が 1 枚など)None。"""
    x0, _, x1, _ = _box(leaf)
    if x1 <= cx:
        return "l"
    if x0 >= cx:
        return "r"
    share = _left_share(leaf, cx)
    if share is None:  # 画素が読めなければ枠の位置で
        share = (cx - x0) / (x1 - x0)
    if share >= SIDE_SHARE:
        return "l"
    if share <= 1 - SIDE_SHARE:
        return "r"
    return None


LIMB_JOINT_INSIDE = 0.6  # 肘・膝のレイヤーがこれ以上、上腕・太ももの範囲に入っていれば上腕・太ももと一緒


def _resolve_limbs(found: list[tuple[Leaf, str, str, str]]) -> list[Pick]:
    """手足の部位を決める。found は (レイヤー, 手足 arm/leg/hand/foot, 左右 l/r, 部位 upper/lower/""/"?"/"joint")。

    - 部位の分からない腕・脚・袖("?")は、同じ側の上腕・前腕(太もも・すね)のうち重なりの大きい方。無ければ上腕・太もも
    - 肘・膝("joint")は、上腕・太ももの範囲に収まっていればそちら、はみ出していれば前腕・すね
    """
    def role(limb: str, side: str, seg: str) -> str:
        return f"{limb}_{side}" + (f"_{seg}" if seg else "")

    boxes: dict[str, list[Box]] = {}
    for leaf, limb, side, seg in found:
        if seg in ("upper", "lower"):
            boxes.setdefault(role(limb, side, seg), []).append(_box(leaf))

    def overlap(b: Box, others: list[Box]) -> float:
        return max((_inside(b, o) for o in others), default=0.0)

    out: dict[int, str] = {}
    for leaf, limb, side, seg in found:
        if seg == "?":
            b = _box(leaf)
            up, low = boxes.get(role(limb, side, "upper"), []), boxes.get(role(limb, side, "lower"), [])
            seg = "lower" if overlap(b, low) > overlap(b, up) else "upper"
            out[leaf.index] = role(limb, side, seg)
            if not up and not low:
                boxes.setdefault(role(limb, side, "upper"), []).append(b)  # 腕・脚 1 枚 → 肘・膝はこれに合わせる
        elif seg != "joint":
            out[leaf.index] = role(limb, side, seg)
    for leaf, limb, side, seg in found:
        if seg == "joint":
            up = _union(boxes.get(role(limb, side, "upper"), []))
            has_lower = bool(boxes.get(role(limb, side, "lower")))
            inside = up is not None and _inside(_box(leaf), up) >= LIMB_JOINT_INSIDE
            out[leaf.index] = role(limb, side, "upper" if inside or (up is None and not has_lower) else "lower")
    return [Pick(leaf, out[leaf.index], ROLE_DEFAULTS[out[leaf.index]][1], how=HOW_NAME) for leaf, *_ in found]


def classify(psd, items: list[Leaf]) -> list[Pick]:
    """クリップ以外の全レイヤーを、命名規則 → 名前 → 位置と重なり順の順に振り分ける。"""
    out: list[Pick] = []
    guessed: list[tuple[Leaf, str | None, int]] = []
    for leaf in items:
        if is_clipping(leaf.layer):
            continue  # 元のレイヤーと一緒に合成される
        names = (*leaf.parents, leaf.layer.name)  # 外側 → 内側
        if any(n.startswith(("_", "#")) for n in names):
            out.append(Pick(leaf, None, how="名前が _ か # で始まる", quiet=True))  # 下書き・参考用
            continue
        parsed = next((p for p in map(parse_part_name, names) if p), None)  # 外側のグループの名前を優先
        if not leaf.visible:
            out.append(Pick(leaf, None, how="非表示"))
            continue
        if parsed:
            role, base, depth = parsed
            explicit = depth is not None
            depth = max(-1.0, min(1.0, depth)) if explicit else ROLE_DEFAULTS[role][1]
            part = None if role in FUNCTIONAL_ROLES else base
            out.append(Pick(leaf, role, depth, explicit, part, HOW_NAMED))
            continue
        if any(BACKGROUND.search(n) for n in names):
            out.append(Pick(leaf, None, how="背景と判断"))
            continue
        if any(NOT_DEFAULT.search(n) for n in names):
            out.append(Pick(leaf, None, how="差分・ガイドと判断"))
            continue
        if any(ROUGH.search(n) for n in names):
            out.append(Pick(leaf, None, how="下書き・見本と判断"))
            continue
        guessed.append((leaf, *guess_with_level(names[::-1])))

    # 顔の範囲・重なり順・左右の基準
    face = [p.leaf for p in out if p.role == "face"] + [leaf for leaf, k, _ in guessed if k == "face"]
    head_box = _union(map(_box, face))
    face_first = min((leaf.index for leaf in face), default=-1)
    face_last = max((leaf.index for leaf in face), default=-1)
    outline_box = _union(map(_box, _face_outline(face))) or head_box
    face_cx = (outline_box[0] + outline_box[2]) / 2 if outline_box else psd.width / 2

    def in_head(b: Box) -> bool:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return head_box is not None and head_box[0] <= cx <= head_box[2] and head_box[1] <= cy <= head_box[3]

    pending: list[Leaf] = []
    limbs: list[tuple[Leaf, str, str, str]] = []
    for leaf, kind, level in guessed:
        if kind is None or (kind == "body" and level > 0 and in_head(_box(leaf))):
            pending.append(leaf)  # 親グループの名前だけで体と分かるが頭にあるもの(「しっぽと後ろ系」の頭のリボンなど)は位置で
            continue
        if kind == "hair":
            kind = "back_hair" if leaf.index < face_first else "front_hair"
        if kind == "side_hair":
            if leaf.index < face_first:  # 顔より奥の横髪(横髪の裏など)
                out.append(Pick(leaf, "back_hair", ROLE_DEFAULTS["back_hair"][1], how=HOW_NAME))
            else:
                out.append(Pick(leaf, "front_hair", SIDE_HAIR_DEPTH, how=HOW_NAME))
            continue
        if "?" in kind:
            if kind.startswith(LIMB_PREFIXES):
                side = _side(leaf, face_cx)
                if side is None:
                    out.append(Pick(leaf, "body", ROLE_DEFAULTS["body"][1], how=HOW_NAME))  # 両腕・両脚が 1 枚 → 曲げられないので体
                    continue
                limb, _, seg = kind.partition("_?")
                limbs.append((leaf, limb, side, seg.lstrip("_") or ("" if limb in ("hand", "foot") else "?")))
                continue
            x0, _, x1, _ = _box(leaf)
            kind = kind.replace("?", "l" if (x0 + x1) / 2 < face_cx else "r")  # キャンバスの左側が l
        out.append(Pick(leaf, kind, ROLE_DEFAULTS[kind][1], how=HOW_NAME))
    out += _resolve_limbs(limbs)

    # 口に隠しがあれば、名前から開いた口にした下唇・唇の線も閉じた口にする(口を開くと一緒に消える)
    if any(p.role == "mouth_closed" and _is_mouth_cover(p.leaf) for p in out):
        for i, p in enumerate(out):
            if p.role == "mouth_open" and p.how == HOW_NAME and not MOUTH_INSIDE.search(p.leaf.layer.name):
                out[i] = Pick(p.leaf, "mouth_closed", ROLE_DEFAULTS["mouth_closed"][1], how=HOW_NAME)

    # 名前で分からないレイヤー: 目・口の範囲に収まるものは差分、顔の範囲なら頭、それ以外は体
    areas = [
        _union(_box(p.leaf) for p in out if p.role in roles)
        for roles in (
            ("mouth_open", "mouth_closed"),
            ("eye_l_white", "eye_l_pupil", "eye_l_lash", "eye_l_shadow", "eye_l_highlight"),
            ("eye_r_white", "eye_r_pupil", "eye_r_lash", "eye_r_shadow", "eye_r_highlight"),
        )
    ]

    def area(b: Box) -> int:
        return (b[2] - b[0]) * (b[3] - b[1])

    bottom = min((leaf.index for leaf in items if leaf.visible and not is_clipping(leaf.layer)), default=-1)
    for leaf in pending:
        b = _box(leaf)
        if leaf.index == bottom and area(b) >= BACKGROUND_COVER * psd.width * psd.height:
            out.append(Pick(leaf, None, how="一番奥でキャンバス全体を覆うので、背景と判断"))
            continue
        unnamed = bool(DEFAULT_LAYER_NAME.match(leaf.layer.name.strip()))
        if any(a and _inside(b, a) >= VARIANT_INSIDE and (unnamed or area(b) >= VARIANT_AREA * area(a)) for a in areas):
            out.append(Pick(leaf, None, how="目・口の範囲にある名前の分からないレイヤーなので、差分と判断"))
            continue
        if not in_head(b):
            role, depth = "body", ROLE_DEFAULTS["body"][1]
        elif leaf.index < face_first:
            role, depth = "back_hair", ROLE_DEFAULTS["back_hair"][1]
        elif leaf.index <= face_last:
            role, depth = "face", ROLE_DEFAULTS["face"][1]
        else:
            role, depth = "face", FACE_ACCESSORY_DEPTH
        out.append(Pick(leaf, role, depth, how=HOW_POSITION))
    return sorted(out, key=lambda p: p.leaf.index)


def _is_mouth_cover(leaf: Leaf) -> bool:
    return bool(re.search(MOUTH_COVER, leaf.layer.name, re.I))


def _face_outline(face: list[Leaf]) -> list[Leaf]:
    """首の付け根を決める顔のレイヤー(耳などを除く)。"""
    return [leaf for leaf in face if any(FACE_OUTLINE.search(n) for n in (leaf.layer.name, *leaf.parents[-1:]))]


def picks_from_mapping(m: dict, items: list[Leaf]) -> list[Pick]:
    """既存の対応表を、レイヤーごとの振り分けに戻す(修正画面で開き直すため)。"""
    by_index: dict[int, Pick] = {}
    for entry in m.get("layers", []):
        role = entry.get("role")
        if role not in ROLES:
            continue
        for leaf in match_sources(entry, items, []):
            part = None if role in FUNCTIONAL_ROLES else entry["name"]
            by_index.setdefault(leaf.index, Pick(leaf, role, float(entry["depth"]), True, part, HOW_MAPPING))
    out = []
    for leaf in items:
        if is_clipping(leaf.layer):
            continue
        out.append(by_index.get(leaf.index) or Pick(leaf, None, how="非表示" if not leaf.visible else "対応表に無い"))
    return out


def build_mapping(psd_path: Path, psd, picks: list[Pick], exprs: list[Expr] | None = None,
                  mouth_frames: bool = False) -> dict:
    """振り分けから対応表を作る。exprs は表情(部品を 1 つも選んでいない表情は書かない)。"""
    used = [p for p in picks if p.role]
    by_index = {p.leaf.index: p.leaf for p in picks}
    step = max(1, -(-psd.height // MAX_AUTO_HEIGHT))
    m: dict = {"psd": str(psd_path), "scale": 1 / step}
    # 部品名の決まった face があれば、首の付け根は Unity の既定(最初の face の下端中央)に任せる
    face = [p.leaf for p in used if p.role == "face" and p.part is None and p.depth == ROLE_DEFAULTS["face"][1]]
    box = _union(map(_box, _face_outline(face) or face))
    if box and not any(p.role == "face" and p.part for p in used):
        m["head_pivot"] = [round((box[0] + box[2]) / 2), box[3]]  # 顔の下端中央
    m["layers"] = _group_picks(used)
    if mouth_frames:
        m["mouth_frames"] = True
    if exprs:
        m["expressions"] = [x.to_json(by_index) for x in exprs if x.slots]
    cover = not mouth_frames and any(p.role == "mouth_closed" and _is_mouth_cover(p.leaf) for p in used)
    for e in m["layers"]:
        if e["role"] == "mouth_closed" and cover:
            e["hide_when_open"] = True
    in_expr = {i for x in exprs or [] for ids in x.slots.values() for i in ids}
    m["unused"] = [f"{p.leaf.path}({p.how})" for p in sorted(picks, key=lambda p: p.leaf.index)
                   if p.role is None and not p.quiet and p.leaf.index not in in_expr]
    return m


def auto_mapping(psd_path: Path, psd, items: list[Leaf]) -> tuple[dict, list[str]]:
    """全レイヤーを部品に振り分けた対応表を作る。戻り値は (mapping, 使わなかったレイヤーと理由の一覧)。"""
    picks = classify(psd, items)
    exprs, frames = auto_expressions(psd, items, picks)
    m = build_mapping(psd_path, psd, picks, exprs, frames)
    return m, m["unused"]


def _group_picks(picks: list[Pick]) -> list[dict]:
    """重なり順を保ったまま部品にまとめる。

    - 機能部品(目・眉・口・鼻)は役割ごとに 1 つ。最後のレイヤーの位置に置く(まつ毛が白目より手前になるように)
    - 命名規則の部品は部品名ごとに 1 つ。最初のレイヤーの位置に置く
    - 自動の部品は PSD のレイヤーごとに 1 つ(Unity の「表示順」でレイヤーごとに重なりを変えられるように。SPEC §5.12)。
      役割・奥行きは、同じ役割・奥行きで続いているレイヤーの並び(顔に挟まれた頭の部品は顔)の最初のレイヤーに合わせる
    """
    picks = sorted(picks, key=lambda p: p.leaf.index)

    def key_of(p: Pick) -> tuple:
        if p.role in FUNCTIONAL_ROLES:
            return ("f", p.role)
        if p.part is not None:
            return ("n", p.part.lower())
        return ("a", p.role, p.depth)

    segs: list[tuple[tuple, list[Pick]]] = []
    for p in picks:
        k = key_of(p)
        if segs and segs[-1][0] == k:
            segs[-1][1].append(p)
        else:
            segs.append((k, [p]))

    # 顔のレイヤーに挟まれた頭の部品(顔に落ちる前髪の影など)は顔に含める(重なり順を崩さないため)
    i = 1
    while i < len(segs) - 1:
        (ka, a), (km, mid), (kc, c) = segs[i - 1], segs[i], segs[i + 1]
        if ka == kc and ka[:2] == ("a", "face") and km[0] == "a" and ROLE_DEFAULTS[km[1]][0] == "head":
            segs[i - 1] = (ka, a + mid + c)
            del segs[i:i + 2]
        else:
            i += 1

    parts: dict[tuple, dict] = {}
    for n, (k, ps) in enumerate(segs):
        if k[0] == "a":
            depth = next((p.depth for p in ps if p.explicit), ps[0].depth)
            # 自動の部品はレイヤーごとに別。ただし通常以外の描画モード(乗算など)のレイヤーは、
            # 下のレイヤーと合成した絵が要るので 1 つ奥のレイヤーと同じ部品にする
            chunks: list[list[Pick]] = []
            for p in ps:
                if chunks and blend_name(p.leaf.layer) != "NORMAL":
                    chunks[-1].append(p)
                else:
                    chunks.append([p])
            for j, chunk in enumerate(chunks):
                kj = (*k, n, j)
                parts[kj] = {"key": kj, "picks": chunk, "role": ps[0].role, "depth": depth}
            continue
        e = parts.setdefault(k, {"key": k, "picks": []})
        e["picks"] += ps
    ordered = sorted(parts.values(), key=lambda e: (
        e["picks"][-1].leaf.index if e["key"][0] == "f" else e["picks"][0].leaf.index))

    used: set[str] = {e["key"][1] for e in ordered if e["key"][0] == "f"}
    used |= {_safe_name(e["picks"][0].part).lower() for e in ordered if e["key"][0] == "n"}
    out = []
    for e in ordered:
        k, ps = e["key"], e["picks"]
        role = e.get("role", ps[0].role)
        if k[0] == "f":
            name = role
        elif k[0] == "n":
            name = _safe_name(ps[0].part)
        else:
            name, n = role, 2
            while name.lower() in used:
                name, n = f"{role}_{n}", n + 1
            used.add(name.lower())
        depth = e["depth"] if "depth" in e else next((p.depth for p in ps if p.explicit), ps[0].depth)
        sources = list(dict.fromkeys(_glob_escape(p.leaf.path) for p in ps))
        entry = {"name": name, "role": role, "group": ROLE_DEFAULTS[role][0], "depth": depth, "sources": sources}
        if any(not p.leaf.visible for p in ps):
            entry["include_hidden"] = True  # 差し替えの絵(閉じた目・口)は非表示のことが多い
        out.append(entry)
    return out


# --- 表情(docs/SPEC.md §5.9) ------------------------------------------------------------


@dataclass
class Expr:
    """表情 1 つ。slots は枠(EXPR_SLOTS)→ 使うレイヤー(Leaf.index)。選ばなかった枠は通常の部品を使う。"""

    name: str
    slots: dict[str, list[int]] = field(default_factory=dict)

    def to_json(self, by_index: dict[int, Leaf]) -> dict:
        parts = {}
        for slot in EXPR_SLOTS:
            ids = sorted(i for i in self.slots.get(slot, []) if i in by_index)
            if ids:
                parts[slot] = [_glob_escape(by_index[i].path) for i in ids]
        return {"name": self.name, "parts": parts}


def expressions_from_mapping(m: dict, items: list[Leaf]) -> tuple[list[Expr], bool]:
    """対応表の表情を、レイヤーの番号で持つ形に戻す(修正画面で開き直すため)。"""
    out = []
    for e in m.get("expressions", []):
        x = Expr(str(e.get("name", "")))
        for slot, sources in (e.get("parts") or {}).items():
            if slot in EXPR_SLOTS:
                entry = {"name": x.name, "sources": sources, "include_hidden": True}
                x.slots[slot] = [leaf.index for leaf in match_sources(entry, items, [])]
        out.append(x)
    return out, bool(m.get("mouth_frames"))


def _expr_label(name: str) -> str:
    """「閉じた笑顔左まぶた」→「笑顔」、「尖った口開き」→「尖った」。表情の名前の候補。"""
    return LABEL_NOISE.sub("", name)


# 口の絵を部品に分けたときの名前(「口閉じ線」「口開き色」)。表情の名前の候補から除く
MOUTH_PART_WORDS = re.compile(
    r"線画|線|色|塗り|塗|影|ハイライト|ベース|[上下]$|\d+$|(?<![a-z])(hi|line|base|colou?r|shadow|highlight)(?![a-z])", re.I)


def _mouth_frame(name: str) -> str | None:
    """口の差し替えの絵なら、その枠(mouth_closed / mouth_half / mouth_open)。"""
    if not MOUTH_WORD.search(name):
        return None
    return next((slot for slot, pattern in FRAME_WORDS if pattern.search(name)), None)


def auto_expressions(psd, items: list[Leaf], picks: list[Pick]) -> tuple[list[Expr], bool]:
    """名前から、口の差し替えの絵・まばたきの閉じた目の絵・表情を見つける。picks(通常の部品)も書き換える。

    - 「表情:名前」グループ(命名規則)の中は、その表情の部品(枠は名前か位置から)
    - 前の版の笑顔の役割名(eye_l_smile など)は表情「笑顔」
    - 口の名前に 閉じ・半開き・開き があれば差し替えの絵。表示されている口と同じ名前の組が通常の口、
      それ以外の名前の組(「尖った口閉じ」と「尖った口開き」など)はその名前の表情の口
    - 閉じた目の絵(「閉じた左まぶた」)は通常のまばたき用。名前に表情の名前があれば(「閉じた笑顔左まぶた」)その表情の目
    - 「笑顔」グループの中の目・口など、笑顔の名前の付いたものは表情「笑顔」
    戻り値は (表情, 口が差し替えの絵か)。
    """
    by_index = {p.leaf.index: p for p in picks}
    face = [p.leaf for p in picks if p.role == "face"]
    box = _union(map(_box, _face_outline(face) or face))
    face_cx = (box[0] + box[2]) / 2 if box else psd.width / 2

    def side(leaf: Leaf) -> str:
        x0, _, x1, _ = _box(leaf)
        return "eye_l" if (x0 + x1) / 2 < face_cx else "eye_r"

    exprs: dict[str, Expr] = {}

    def add(name: str, slot: str, leaf: Leaf) -> None:
        x = exprs.setdefault(name, Expr(name))
        x.slots.setdefault(slot, []).append(leaf.index)
        p = by_index.get(leaf.index)
        if p is not None and (p.role is None or p.how in (HOW_NAME, HOW_POSITION)):
            by_index[leaf.index] = _unpick(p, f"表情「{name}」で使う")

    def slot_of(leaf: Leaf, names: tuple[str, ...]) -> str:
        for n in names[::-1]:
            low = n.strip().lower()
            if low in EXPR_SLOTS:
                return low
            frame = _mouth_frame(n)
            if frame:
                return frame
            if MOUTH_WORD.search(n):
                return "mouth_closed"
            if EYE_WORD.search(n):
                return side(leaf)
        return "extra"

    mouths: list[tuple[Leaf, str, str]] = []  # (レイヤー, 名前の候補, 枠)
    eyes: list[tuple[Leaf, str]] = []
    smiles: list[tuple[Leaf, tuple[str, ...]]] = []
    for leaf in reversed(items):  # PSD の上から(表情の並び順)
        if is_clipping(leaf.layer):
            continue
        names = (*leaf.parents, leaf.layer.name)
        if any(n.startswith(("_", "#")) or ROUGH.search(n) for n in names):
            continue  # 下書き・参考用・見本
        group = next((EXPR_GROUP.match(n).group(1) for n in names if EXPR_GROUP.match(n)), None)
        if group:
            inner = names[next(i for i, n in enumerate(names) if EXPR_GROUP.match(n)) + 1:]
            add(group, slot_of(leaf, inner), leaf)
            continue
        legacy = next((LEGACY_SMILE[n.strip().lower()] for n in names if n.strip().lower() in LEGACY_SMILE), None)
        if legacy:
            add("笑顔", legacy, leaf)
            continue
        p = by_index.get(leaf.index)
        if p is not None and p.how == HOW_NAMED:
            continue  # 役割名の付いた通常の部品
        name = leaf.layer.name
        frame = _mouth_frame(name)
        if frame:
            label = _expr_label(name)
            core = MOUTH_PART_WORDS.sub("", label)
            if label and not core:
                continue  # 口の絵の一部(「口閉じ線下」など)。表情の名前ではない
            mouths.append((leaf, core, frame))
        elif CLOSED_WORD.search(name) and EYE_WORD.search(name) and not leaf.visible:
            eyes.append((leaf, _expr_label(name)))
        elif any(SMILE_NAME.search(n) for n in names) and not leaf.visible:
            smiles.append((leaf, names))

    # 口: 表示されている口の名前の組が通常。差し替えの絵が 1 枚も無ければ今までどおり(伸び縮み)
    visible = [label for leaf, label, _ in mouths if leaf.visible]
    normal = max(set(visible), key=visible.count) if visible else None
    frames = normal is not None and any(not leaf.visible and label == normal for leaf, label, _ in mouths)
    for leaf, label, frame in mouths:
        if frames and label == normal:
            p = by_index.get(leaf.index)
            if p is not None:
                by_index[leaf.index] = Pick(leaf, frame, ROLE_DEFAULTS[frame][1], how=HOW_NAME)
        elif not leaf.visible:
            add(label or f"表情{len(exprs) + 1}", frame, leaf)
    # 閉じた目: 名前が無ければまばたき、表情の名前(笑顔など)があればその表情
    for leaf, label in eyes:
        s = side(leaf)
        if not label:
            p = by_index.get(leaf.index)
            if p is not None and p.role is None:
                by_index[leaf.index] = Pick(leaf, f"{s}_closed", ROLE_DEFAULTS[f"{s}_closed"][1], how=HOW_NAME)
        elif label in exprs or label in (normal or "") or SMILE_NAME.search(label):
            add(SMILE_NAME.search(label).group(0) if label not in exprs and SMILE_NAME.search(label) else label, s, leaf)
        else:
            p = by_index.get(leaf.index)
            if p is not None and p.role is None:
                by_index[leaf.index] = _unpick(p, "表情用の目(確認画面で表情に割り当てられます)")
    for leaf, names in smiles:
        word = next(SMILE_NAME.search(n).group(0) for n in names if SMILE_NAME.search(n))
        add(exprs.get(word, Expr(word)).name if word in exprs else word, slot_of(leaf, names), leaf)

    picks[:] = [by_index.get(p.leaf.index, p) for p in picks]
    return list(exprs.values())[:MAX_EXPRESSIONS], frames


def _unpick(p: Pick, how: str) -> Pick:
    return Pick(p.leaf, None, how=how)


EXPR_ANCHORS = {  # 表情の枠 → 重なり順で直後に置く通常の部品(最後に見つかったもの)
    "eye_l": tuple(r for r in ROLES if r.startswith("eye_l_")),
    "eye_r": tuple(r for r in ROLES if r.startswith("eye_r_")),
    "mouth_closed": ("mouth_open", "mouth_half", "mouth_closed"),
    "mouth_half": ("mouth_open", "mouth_half", "mouth_closed"),
    "mouth_open": ("mouth_open", "mouth_half", "mouth_closed"),
}


def expression_entries(m: dict) -> list[dict]:
    """対応表の通常の部品に表情の部品を足し、書き出す順(奥 → 手前)に並べる。

    表情の部品は、入れ替える通常の部品の直後に置く(差分が PSD の一番上にあっても前髪より手前にしない)。
    追加パーツは、目・眉・口より奥で一番手前の顔の部品の直後に置く。入れ替える部品が無ければ一番手前。
    """
    out = [dict(e) for e in m["layers"]]
    for k, x in enumerate(m.get("expressions", []), start=1):
        for slot in EXPR_SLOTS:
            sources = (x.get("parts") or {}).get(slot)
            if not sources:
                continue
            e = {"name": f"expr{k}_{slot}", "role": f"expr_{slot}", "group": "head", "depth": EXPR_SLOT_DEPTH[slot],
                 "sources": list(sources), "include_hidden": True, "expr": k}
            if slot == "extra":
                first = next((i for i, y in enumerate(out) if y["role"].startswith(("eye_", "brow_", "mouth_"))), len(out))
                faces = [i for i, y in enumerate(out[:first]) if y["role"] == "face"]
                at = faces[-1] if faces else None
            else:
                anchors = [i for i, y in enumerate(out) if y["role"] in EXPR_ANCHORS[slot]
                           or y["role"] == f"expr_{slot}" or (slot.startswith("mouth") and y["role"].startswith("expr_mouth"))]
                at = anchors[-1] if anchors else None
            out.insert(len(out) if at is None else at + 1, e)
    return out


def describe_parts(m: dict) -> list[str]:
    """部品ごとに、役割・枚数・元のグループ(一番外側)を 1 行で。"""
    lines = []
    for e in m["layers"]:
        tops = list(dict.fromkeys(s.split("/")[0] for s in e["sources"]))
        where = "、".join(tops[:4]) + (" など" if len(tops) > 4 else "")
        lines.append(f"  {e['name']:<14} {ROLE_LABELS.get(e['role'], e['role'])}  {len(e['sources'])} 枚({where})")
    return lines


def _open_for_auto(args: argparse.Namespace):
    """auto / edit 共通: PSD を読み、出力フォルダとレイヤー一覧を用意する。失敗したら None。"""
    psd_path = Path(args.psd).resolve()
    if not psd_path.is_file():
        print(f"PSD が見つかりません: {psd_path}")
        return None
    out = Path(args.out).resolve() if args.out else psd_path.parent / f"{psd_path.stem}_FlatRig"
    out.mkdir(parents=True, exist_ok=True)
    print(f"読み込み中: {psd_path.name}")
    psd = open_psd(psd_path)
    items = leaves(psd)
    (out / "psd_layers.txt").write_text(layer_table(psd_path, psd, items), encoding="utf-8")
    return psd_path, out, psd, items


def cmd_auto(args: argparse.Namespace) -> int:
    opened = _open_for_auto(args)
    if opened is None:
        return 1
    psd_path, out, psd, items = opened
    mapping_path = out / "mapping.json"
    if mapping_path.exists():
        print(f"既にある対応表を使います: {mapping_path}")
        print("(PSD のレイヤーを変えたときは、mapping.json を削除してからもう一度実行すると作り直します)")
        m = json.loads(mapping_path.read_text(encoding="utf-8"))
    else:
        m, unused = auto_mapping(psd_path, psd, items)
        mapping_path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"部品 {len(m['layers'])} 個に分けました(奥 → 手前):")
        print("\n".join(describe_parts(m)))
        for path in unused:
            print(f"注意: 使わないレイヤー: {path}")
        roles = {e["role"] for e in m["layers"]}
        missing = [r for r in RECOMMENDED_ROLES if r not in roles]
        if missing:
            print("注意: 次の部品がありません(その動きは付きません): " + ", ".join(ROLE_LABELS[r] for r in missing))
        if m.get("mouth_frames"):
            frames = [ROLE_LABELS[r] for r in ("mouth_closed", "mouth_half", "mouth_open") if r in roles]
            print("口は絵の差し替え: " + "・".join(frames))
        for x in m.get("expressions", []):
            slots = "・".join(EXPR_SLOT_LABELS[s] for s in EXPR_SLOTS if s in x.get("parts", {}))
            print(f"表情「{x['name']}」: {slots}(Unity の表情メニュー・ジェスチャーで切り替わります)")
        if "face" not in roles:
            print("エラー: 顔の部品が見つかりません。顔のレイヤーかグループの名前を face にしてください"
                  "(頭の回転の中心を決めるため)")
            return 1
        print(f"分け方を直すときは {mapping_path.name} を直すか、レイヤー名を命名規則にして {mapping_path.name} を"
              "削除してから、もう一度実行してください。")
    return export_mapping(m, mapping_path.parent, out / "layers")


def cmd_edit(args: argparse.Namespace) -> int:
    """修正画面で部品分けを確認・修正してから書き出す(FlatRigPSD.exe に PSD をドロップしたとき)。"""
    try:
        import tkinter  # noqa: F401

        import psd_editor
    except ImportError:
        print("部品の確認画面を開けないため、自動の分け方で書き出します。")
        return cmd_auto(args)
    opened = _open_for_auto(args)
    if opened is None:
        return 1
    psd_path, out, psd, items = opened
    mapping_path = out / "mapping.json"
    if mapping_path.exists():
        print(f"前回の対応表を開きます: {mapping_path}")
        m = json.loads(mapping_path.read_text(encoding="utf-8"))
        picks = picks_from_mapping(m, items)
        exprs, frames = expressions_from_mapping(m, items)
    else:
        picks = classify(psd, items)
        exprs, frames = auto_expressions(psd, items, picks)
    print("部品の確認画面を開きます")
    if psd_editor.run(psd_path, psd, items, picks, out, exprs, frames):
        return 0
    print(CANCELLED_MESSAGE)
    return 1


# --- export -------------------------------------------------------------------------


def _to_float(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0


def _blend(mode: str, cb: np.ndarray, cs: np.ndarray) -> np.ndarray:
    if mode == "MULTIPLY":
        return cb * cs
    if mode == "SCREEN":
        return cb + cs - cb * cs
    if mode in ("LINEAR_DODGE", "ADD"):
        return np.minimum(1.0, cb + cs)
    if mode == "OVERLAY":
        return np.where(cb <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    return cs


SUPPORTED_MODES = {"NORMAL", "MULTIPLY", "SCREEN", "LINEAR_DODGE", "ADD", "OVERLAY", "PASS_THROUGH"}


def composite_over(dst: np.ndarray, src: np.ndarray, mode: str, clip_alpha: np.ndarray | None = None) -> None:
    """dst(RGBA, 0〜1, 非乗算)に src を重ねる。clip_alpha があればその範囲だけに描き、dst のアルファは変えない。"""
    sa = src[..., 3:4]
    cb, cs = dst[..., :3], src[..., :3]
    if clip_alpha is not None:
        sa = sa * clip_alpha
        dst[..., :3] = cb * (1 - sa) + _blend(mode, cb, cs) * sa
        return
    ba = dst[..., 3:4]
    mixed = (1 - ba) * cs + ba * _blend(mode, cb, cs)
    ao = sa + ba * (1 - sa)
    co = sa * mixed + (1 - sa) * ba * cb
    dst[..., :3] = np.divide(co, ao, out=np.zeros_like(co), where=ao > 1e-6)
    dst[..., 3:4] = ao


@dataclass(frozen=True)
class Region:
    """PSD 座標の矩形(x1, y1 は含まない)。縮小時にずれないよう、縮小の倍数にそろえて使う。"""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def size(self) -> tuple[int, int]:
        return self.x1 - self.x0, self.y1 - self.y0

    def empty(self) -> np.ndarray:
        w, h = self.size
        return np.zeros((h, w, 4), dtype=np.float32)


BAND_PIXELS = 8_000_000  # 1 回に合成する画素数の上限(大きな PSD でメモリを使いすぎないよう、横長の帯に分ける)
_topil_cache: dict[int, Image.Image | None] = {}  # 帯ごとに同じレイヤーを読み直さないため(部品 1 つの間だけ持つ)


def _topil(layer) -> Image.Image | None:
    key = id(layer)
    if key not in _topil_cache:
        _topil_cache[key] = layer.topil()
    return _topil_cache[key]


def render_layer(layer, region: Region, warnings: list[str], path: str) -> np.ndarray | None:
    """レイヤー 1 枚を region の RGBA 配列にする(不透明度とマスクを反映)。"""
    img = _topil(layer)
    if img is None:
        return None
    arr = region.empty()
    x0, y0 = layer.bbox[:2]
    # region に入る部分だけを浮動小数にする(大きなレイヤーでもメモリを抑える)
    cx0, cy0 = max(0, region.x0 - x0), max(0, region.y0 - y0)
    cx1, cy1 = min(img.width, region.x1 - x0), min(img.height, region.y1 - y0)
    if cx1 <= cx0 or cy1 <= cy0:
        return arr
    _paste(arr, _to_float(img.crop((cx0, cy0, cx1, cy1))), x0 + cx0 - region.x0, y0 + cy0 - region.y0)
    arr[..., 3] *= layer.opacity / 255.0
    mask = getattr(layer, "mask", None)
    if mask is not None and layer.has_mask() and not getattr(mask, "disabled", False):
        try:
            w, h = region.size
            m = np.full((h, w, 1), getattr(mask, "background_color", 0) / 255.0, dtype=np.float32)
            mimg = mask.topil()
            if mimg is not None:
                mx0, my0 = mask.bbox[:2]
                kx0, ky0 = max(0, region.x0 - mx0), max(0, region.y0 - my0)
                kx1, ky1 = min(mimg.width, region.x1 - mx0), min(mimg.height, region.y1 - my0)
                if kx1 > kx0 and ky1 > ky0:
                    crop = mimg.convert("L").crop((kx0, ky0, kx1, ky1))
                    marr = np.asarray(crop, dtype=np.float32)[..., None] / 255.0
                    _paste(m, marr, mx0 + kx0 - region.x0, my0 + ky0 - region.y0)
            arr[..., 3:4] *= m
        except Exception as exc:  # マスクの形式が想定外でも書き出しは続ける
            warnings.append(f"{path}: レイヤーマスクを反映できませんでした({exc})")
    return arr


def _paste(dst: np.ndarray, src: np.ndarray, x0: int, y0: int) -> None:
    h, w = dst.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(w, x0 + src.shape[1]), min(h, y0 + src.shape[0])
    if dx1 <= dx0 or dy1 <= dy0:
        return
    dst[dy0:dy1, dx0:dx1] = src[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]


def match_sources(entry: dict, items: list[Leaf], warnings: list[str]) -> list[Leaf]:
    include_hidden = bool(entry.get("include_hidden", False))
    matched: list[Leaf] = []
    for pattern in entry["sources"]:
        hits = [leaf for leaf in items if fnmatch.fnmatchcase(leaf.path, pattern) and not is_clipping(leaf.layer)]
        if not hits:
            warnings.append(f"{entry['name']}: 「{pattern}」に一致するレイヤーがありません")
        matched += [h for h in hits if h not in matched]
    used = []
    for leaf in sorted(matched, key=lambda x: x.index):
        if not leaf.visible and not include_hidden:
            warnings.append(f"{entry['name']}: 非表示の「{leaf.path}」は使いません(使うなら include_hidden)")
            continue
        used.append(leaf)
    return used


def source_region(leaves_: list[Leaf], canvas: tuple[int, int], step: int) -> Region | None:
    boxes = [tuple(leaf.layer.bbox) for leaf in leaves_ if leaf.layer.bbox[2] > leaf.layer.bbox[0]]
    if not boxes:
        return None
    m = EDGE_MARGIN * step
    x0 = max(0, min(b[0] for b in boxes) - m) // step * step
    y0 = max(0, min(b[1] for b in boxes) - m) // step * step
    x1 = -(-min(canvas[0], max(b[2] for b in boxes) + m) // step) * step
    y1 = -(-min(canvas[1], max(b[3] for b in boxes) + m) // step) * step
    return Region(x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def build_output_layer(entry: dict, used: list[Leaf], region: Region, warnings: list[str]) -> np.ndarray:
    """各レイヤーは自分の範囲だけで描いてから region に重ねる(大きな配列を何枚も作らないため)。"""
    include_hidden = bool(entry.get("include_hidden", False))
    out = region.empty()
    for leaf in used:
        x0, y0, x1, y1 = leaf.layer.bbox
        box = Region(max(x0, region.x0), max(y0, region.y0), min(x1, region.x1), min(y1, region.y1))
        if box.x1 <= box.x0 or box.y1 <= box.y0:
            continue
        base = render_layer(leaf.layer, box, warnings, leaf.path)
        if base is None:
            continue
        for clip in getattr(leaf.layer, "clip_layers", None) or []:
            if not clip.visible and not include_hidden:
                continue
            src = render_layer(clip, box, warnings, f"{leaf.path}(クリップ)")
            if src is not None:
                composite_over(base, src, _mode(clip, warnings, leaf.path), clip_alpha=base[..., 3:4].copy())
        view = out[box.y0 - region.y0 : box.y1 - region.y0, box.x0 - region.x0 : box.x1 - region.x0]
        composite_over(view, base, _mode(leaf.layer, warnings, leaf.path))
    return out


def build_downscaled(entry: dict, used: list[Leaf], region: Region, step: int, warnings: list[str]) -> np.ndarray:
    """部品を合成して 1/step にする。大きな部品は横長の帯に分けて合成する(縮小の倍数で切るので結果は同じ)。"""
    w, _ = region.size
    rows = max(step, BAND_PIXELS // max(1, w) // step * step)
    bands = []
    try:
        for y in range(region.y0, region.y1, rows):
            band = Region(region.x0, y, region.x1, min(region.y1, y + rows))
            bands.append(downscale(build_output_layer(entry, used, band, warnings), step))
    finally:
        _topil_cache.clear()
    return np.concatenate(bands, axis=0)


def _mode(layer, warnings: list[str], path: str) -> str:
    mode = blend_name(layer)
    if mode not in SUPPORTED_MODES:
        warnings.append(f"{path}: 描画モード {mode} は通常として扱います")
        return "NORMAL"
    return mode


def downscale(arr: np.ndarray, step: int) -> np.ndarray:
    """1/step に縮小する(面積平均)。乗算済みアルファで平均するので、縁の色が黒く濁らない。"""
    if step == 1:
        return arr
    h, w = arr.shape[0] // step, arr.shape[1] // step
    pm = arr[: h * step, : w * step].copy()
    pm[..., :3] *= pm[..., 3:4]
    pm = pm.reshape(h, step, w, step, 4).mean(axis=(1, 3))
    a = pm[..., 3:4]
    pm[..., :3] = np.divide(pm[..., :3], a, out=np.zeros_like(pm[..., :3]), where=a > 0)
    return np.clip(pm, 0.0, 1.0)


def bleed_edges(arr: np.ndarray, iterations: int = 8) -> np.ndarray:
    """完全に透明な画素に、隣の絵の色を広げる(アルファは変えない)。

    透明部分の色が黒のままだと、テクスチャの補間やミップマップで縁に暗い線が出るため。
    """
    known = arr[..., 3] >= 0.5 / 255  # 8bit にすると 0 になる画素は「色なし」として扱う
    if known.all() or not known.any():
        return arr
    out = arr.copy()
    rgb = out[..., :3]
    h, w = known.shape
    for _ in range(iterations):
        k = np.pad(known.astype(np.float32), 1)
        c = np.pad(rgb * known[..., None], ((1, 1), (1, 1), (0, 0)))
        count = sum(k[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3))
        total = sum(c[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3))
        new = ~known & (count > 0)
        if not new.any():
            break
        rgb[new] = total[new] / count[new][:, None]
        known = known | new
    if not known.all():
        rgb[~known] = rgb[known].mean(axis=0)  # 遠くの透明部分は平均色で埋める
    return out


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def validate_mapping(m: dict) -> list[str]:
    errors = []
    if not isinstance(m.get("layers"), list) or not m["layers"]:
        return ["layers が空です"]
    names, functional = set(), {}
    for i, e in enumerate(m["layers"]):
        label = e.get("name") or f"layers[{i}]"
        if not e.get("name"):
            errors.append(f"{label}: name がありません")
        elif e["name"] in names:
            errors.append(f"{label}: name が重複しています")
        names.add(e.get("name"))
        if e.get("role") not in ROLES:
            errors.append(f"{label}: role は {', '.join(ROLES)} のどれかです")
        elif e["role"] in FUNCTIONAL_ROLES:
            if e["role"] in functional:
                errors.append(f"{label}: role {e['role']} は 1 つだけにしてください({functional[e['role']]} と重複)")
            functional[e["role"]] = label
        if e.get("group") not in ("head", "body"):
            errors.append(f"{label}: group は head か body です")
        if not isinstance(e.get("depth"), (int, float)) or not -1 <= e["depth"] <= 1:
            errors.append(f"{label}: depth は −1〜1 の数値です")
        if not e.get("sources"):
            errors.append(f"{label}: sources が空です")
    exprs = m.get("expressions", [])
    if not isinstance(exprs, list):
        errors.append("expressions は表情の並び(リスト)です")
        exprs = []
    if len(exprs) > MAX_EXPRESSIONS:
        errors.append(f"表情は {MAX_EXPRESSIONS} 個までです(Unity で扱える数)")
    expr_names: set[str] = set()
    for k, x in enumerate(exprs, start=1):
        name = str(x.get("name", "")).strip() if isinstance(x, dict) else ""
        label = f"expressions[{k}]" + (f"「{name}」" if name else "")
        if not name:
            errors.append(f"{label}: name がありません")
        elif name in expr_names:
            errors.append(f"{label}: 表情の名前が重複しています")
        expr_names.add(name)
        parts = x.get("parts") if isinstance(x, dict) else None
        if not isinstance(parts, dict):
            errors.append(f"{label}: parts がありません")
            continue
        for slot, sources in parts.items():
            if slot not in EXPR_SLOTS:
                errors.append(f"{label}: 枠 {slot} は {', '.join(EXPR_SLOTS)} のどれかです")
            elif not isinstance(sources, list) or not sources:
                errors.append(f"{label}: {slot} のレイヤーが空です")
    scale = m.get("scale", DEFAULT_SCALE)
    if not isinstance(scale, (int, float)) or not 0 < scale <= 1 or abs(1 / scale - round(1 / scale)) > 1e-3:
        errors.append("scale は 1, 0.5, 0.25 など「1/整数」にしてください(縮小で位置がずれないように)")
    return errors


def cmd_export(args: argparse.Namespace) -> int:
    mapping_path = Path(args.mapping).resolve()
    m = json.loads(mapping_path.read_text(encoding="utf-8"))
    out = Path(args.out).resolve() if args.out else mapping_path.parent / "layers"
    return export_mapping(m, mapping_path.parent, out)


def export_mapping(m: dict, base_dir: Path, out: Path) -> int:
    errors = validate_mapping(m)
    if errors:
        print("mapping.json のエラー:")
        for e in errors:
            print(f"  - {e}")
        return 1
    psd_path = Path(m["psd"])
    if not psd_path.is_absolute():
        psd_path = base_dir / psd_path  # mapping.json からの相対パスも使える
    print(f"書き出し中: {psd_path.name}")
    psd = open_psd(psd_path)
    items = leaves(psd)
    size = (psd.width, psd.height)
    step = round(1 / float(m.get("scale", DEFAULT_SCALE)))  # 1/整数 の縮小だけを許す(位置がずれないように)
    out.mkdir(parents=True, exist_ok=True)
    canvas_s = (size[0] // step, size[1] // step)

    warnings: list[str] = []
    frames = bool(m.get("mouth_frames"))
    hide_closed = not frames and any(e.get("role") == "mouth_closed" and e.get("hide_when_open") for e in m["layers"])
    # 隠しの構造: 閉じた口の唇の線(隠し以外)の範囲。開いた口はこの線の高さ・幅から開き始める
    lines: dict[str, Box] = {}
    if hide_closed:
        for e in m["layers"]:
            if e.get("role") in MOUTH_PAIRS.values():
                box = _union(_box(leaf) for leaf in match_sources(e, items, [])
                             if leaf.visible and not _is_mouth_cover(leaf))
                if box:
                    lines[e["role"]] = box
    entries, preview = [], Image.new("RGBA", canvas_s, (0, 0, 0, 0))
    for order, entry in enumerate(expression_entries(m)):
        used = match_sources(entry, items, warnings)
        region = source_region(used, size, step)
        if region is None:
            warnings.append(f"{entry['name']}: 使えるレイヤーが無いので書き出しません")
            continue
        img = to_image(bleed_edges(build_downscaled(entry, used, region, step, warnings)))
        ox, oy = region.x0 // step, region.y0 // step
        local = img.getchannel("A").point(lambda a: 255 if a > 2 else 0).getbbox()
        if local is None:
            warnings.append(f"{entry['name']}: 画像が空なので書き出しません")
            continue
        local = (max(0, local[0] - EDGE_MARGIN), max(0, local[1] - EDGE_MARGIN),
                 min(img.width, canvas_s[0] - ox, local[2] + EDGE_MARGIN),
                 min(img.height, canvas_s[1] - oy, local[3] + EDGE_MARGIN))
        bbox = [local[0] + ox, local[1] + oy, local[2] + ox, local[3] + oy]
        file = f"{entry['name']}.png"
        img.crop(local).save(out / file)
        preview.alpha_composite(img, (ox, oy))
        pivot = entry.get("pivot")
        if not pivot and entry["role"] == "mouth_open" and not hide_closed and not frames:
            pivot = [(bbox[0] + bbox[2]) / 2 * step, bbox[1] * step]  # 上端中央から下へ開く
        extra: dict = {"hide_when_open": True} if entry["role"] == "mouth_closed" and entry.get("hide_when_open") else {}
        # 隠しの構造では閉じた口が消えるので、口の中は唇の線の高さから上下に開き、
        # 開き始めは線の幅に合わせる(口の中が隠しより先に口角からはみ出さないように)
        line = lines.get(MOUTH_PAIRS.get(entry["role"], ""))
        if line and not pivot:
            pivot = [(bbox[0] + bbox[2]) / 2 * step, (line[1] + line[3]) / 2]
            extra["open_from_width"] = round(min(1.0, (line[2] - line[0]) / ((bbox[2] - bbox[0]) * step)), 3)
        entries.append({
            "name": entry["name"], "role": entry["role"], "group": entry["group"], "file": file,
            "bbox": bbox,
            "pivot": [pivot[0] / step, pivot[1] / step] if pivot else [(bbox[0] + bbox[2]) / 2,
                                                                       (bbox[1] + bbox[3]) / 2],
            "depth": entry["depth"], "order": order, **extra,
        })
        if "expr" in entry:
            entries[-1]["expr"] = entry["expr"]

    doc: dict = {"canvas": list(canvas_s)}
    for key in ("head_pivot", "body_pivot"):
        if m.get(key):
            doc[key] = [m[key][0] / step, m[key][1] / step]
    if frames:
        doc["mouth_frames"] = True
    if m.get("expressions"):
        doc["expressions"] = [x["name"] for x in m["expressions"]]
    doc["layers"] = entries
    (out / "layers.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preview.save(out / "preview.png")

    for w in dict.fromkeys(warnings):
        print(f"注意: {w}")
    print(f"書き出しました: {out}(レイヤー {len(entries)} 枚、キャンバス {canvas_s[0]}x{canvas_s[1]}px)")
    print("preview.png で重なり方を確認してから、Unity の FlatRig ウィンドウで layers.json を選んで「アバターを生成」を押してください。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="レイヤー一覧と mapping.json の下書きを作る")
    p.add_argument("psd")
    p.add_argument("--out", help="出力フォルダ(既定: リポジトリの art/private/<PSD名>)")
    p.add_argument("--force", action="store_true", help="既存の mapping.json を上書きする")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("auto", help="命名規則に沿った PSD を 1 回で書き出す(対応表も自動)")
    p.add_argument("psd")
    p.add_argument("--out", help="出力フォルダ(既定: PSD と同じ場所の <PSD名>_FlatRig)")
    p.set_defaults(func=cmd_auto)
    p = sub.add_parser("edit", help="修正画面で部品分けを確認・修正してから書き出す")
    p.add_argument("psd")
    p.add_argument("--out", help="出力フォルダ(既定: PSD と同じ場所の <PSD名>_FlatRig)")
    p.set_defaults(func=cmd_edit)
    p = sub.add_parser("export", help="mapping.json に従って PNG と layers.json を書き出す")
    p.add_argument("mapping")
    p.add_argument("--out", help="出力フォルダ(既定: mapping.json と同じ場所の layers/)")
    p.set_defaults(func=cmd_export)
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 1 and argv[0].lower().endswith(".psd"):
        argv = ["auto", argv[0]]  # PSD をドロップしたとき
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
