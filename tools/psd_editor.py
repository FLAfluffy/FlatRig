"""PSD の部品分けの修正画面(tkinter)。FlatRigPSD.exe に PSD をドロップしたときに開く。

自動の振り分け(psd_to_layers.classify)を、プレビューを見ながらレイヤーごとに選び直して書き出す。
表情(docs/SPEC.md §5.9)も追加・名前変更・削除でき、表情ごとに目・口・追加パーツに使うレイヤーを選べる。
プレビューは利用者の PC の画面に出すだけで、ファイルには書かない。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
import psd_to_layers as t
from PIL import Image, ImageChops

PREVIEW_MAX_HEIGHT = 760
FADE = 0.15  # 強調表示で、選んでいないレイヤーの濃さ
TINT = 0.55  # 色分け表示で、部品の色を混ぜる割合
MIN_PREVIEW = 160  # ウィンドウを縮めたときも残すプレビューの高さ
UNUSED = "使わない"
MIXED = "(複数)"
NORMAL = "通常"
NOT_IN_EXPR = "この表情では使わない"

# 部品の選択肢: (表示名, 役割, 奥行き。None は役割の標準)
# 並びは部位の順(髪 → 顔 → 眉 → 目 → 口 → 体 → 腕 → 脚)。左右のある部品は左の一式のあとに右の一式。
# 「左」「右」は画面の左右(_l が画面の左)
EYE_PARTS = ("white", "pupil", "highlight", "shadow", "lash", "closed")
LIMB_ORDER = ("arm_l_upper", "arm_l_lower", "hand_l", "arm_r_upper", "arm_r_lower", "hand_r",
              "leg_l_upper", "leg_l_lower", "foot_l", "leg_r_upper", "leg_r_lower", "foot_r")
CHOICES: list[tuple[str, str | None, float | None]] = [
    (UNUSED, None, None),
    ("後ろ髪", "back_hair", None),
    ("前髪", "front_hair", None),
    ("横髪", "front_hair", t.SIDE_HAIR_DEPTH),
    ("顔", "face", None),
    ("顔の飾り(顔より手前)", "face", t.FACE_ACCESSORY_DEPTH),
    (t.ROLE_LABELS["nose"], "nose", None),
    (t.ROLE_LABELS["brow_l"], "brow_l", None),
    (t.ROLE_LABELS["brow_r"], "brow_r", None),
    *((t.ROLE_LABELS[f"eye_{side}_{part}"], f"eye_{side}_{part}", None) for side in "lr" for part in EYE_PARTS),
    ("閉じた口(上唇・唇の線・隠し)", "mouth_closed", None),
    ("半開きの口(絵の差し替え)", "mouth_half", None),
    ("開いた口(口の中・歯・舌・下唇)", "mouth_open", None),
    ("体", "body", None),
    *((t.ROLE_LABELS[r], r, None) for r in LIMB_ORDER),  # 手足(Unity で「手足をトラッキングに合わせる」を ON にすると曲がる)
]
PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46c8c8", "#f032e6", "#9acd32",
    "#ff8fa3", "#008080", "#b8860b", "#9a6324", "#800000", "#2f4f4f", "#000075", "#808000",
]


# 表情の部品の選択肢: (表示名, 枠)
SLOT_CHOICES: list[tuple[str, str | None]] = [(NOT_IN_EXPR, None), *((t.EXPR_SLOT_LABELS[s], s) for s in t.EXPR_SLOTS)]
SWAPPED_ROLES = ("mouth_half", "mouth_open", "eye_l_closed", "eye_r_closed")  # 絵の差し替え(通常の見た目では隠れている)


def slot_of(x: t.Expr, index: int) -> str | None:
    return next((s for s in t.EXPR_SLOTS if index in x.slots.get(s, [])), None)


def set_slot(x: t.Expr, indices: list[int], slot: str | None) -> None:
    """表情 x で、indices のレイヤーを枠 slot に入れる(None なら外す)。1 枚のレイヤーは表情の中で 1 つの枠だけ。"""
    for s in t.EXPR_SLOTS:
        rest = [i for i in x.slots.get(s, []) if i not in indices]
        if s == slot:
            rest += list(indices)
        if rest:
            x.slots[s] = sorted(rest)
        else:
            x.slots.pop(s, None)


SIDE_SWAP = {"l": "r", "r": "l"}


def mirror_role(role: str | None) -> str | None:
    """左右のある役割を反対側にする(eye_l_white ↔ eye_r_white、hand_l ↔ hand_r など)。左右の無い役割はそのまま。"""
    if role is None:
        return None
    parts = role.split("_")
    if len(parts) >= 2 and parts[1] in SIDE_SWAP:
        parts[1] = SIDE_SWAP[parts[1]]
    return "_".join(parts)


def swap_sides(picks: list[t.Pick], exprs: list[t.Expr]) -> int:
    """全体の左右を入れ替える(部品と表情の左目・右目の枠)。入れ替えたレイヤーの数を返す。"""
    n = 0
    for p in picks:
        role = mirror_role(p.role)
        if role != p.role:
            p.role, p.part, p.quiet = role, None, False
            p.how = t.HOW_MANUAL
            n += 1
    for x in exprs:
        left, right = x.slots.pop("eye_l", None), x.slots.pop("eye_r", None)
        if right:
            x.slots["eye_l"] = right
        if left:
            x.slots["eye_r"] = left
        n += len(left or []) + len(right or [])
    return n


def expr_view(picks: list[t.Pick], exprs: list[t.Expr], current: t.Expr | None, frames: bool) -> list[tuple[t.Leaf, str]]:
    """表情 current(None は通常)のときに見えるレイヤーと、その表示名(奥 → 手前)。

    表情が枠を持つと、その枠の通常の部品は隠れる(口はどれか 1 つの枠があれば口全体)。
    差し替えの絵(半開き・開いた口、閉じた目)は閉じた・開いた状態の代表として、閉じた口と開いた目だけを見せる。
    """
    hide: set[str] = set()
    if frames:
        hide |= {"mouth_half", "mouth_open"}
    hide |= {"eye_l_closed", "eye_r_closed"}
    shown_expr: dict[int, str] = {}
    if current is not None:
        for side in ("eye_l", "eye_r"):
            if current.slots.get(side):
                hide |= {r for r in t.ROLES if r.startswith(side + "_")}
        mouth = [s for s in ("mouth_closed", "mouth_half", "mouth_open") if current.slots.get(s)]
        if mouth:
            hide |= {"mouth_closed", "mouth_half", "mouth_open"}
        for s in ("eye_l", "eye_r", "extra", *mouth[:1]):
            for i in current.slots.get(s, []):
                shown_expr[i] = t.EXPR_SLOT_LABELS[s]
    out = []
    for p in picks:
        if p.leaf.index in shown_expr:
            out.append((p.leaf, shown_expr[p.leaf.index]))
        elif p.role is not None and p.role not in hide:
            out.append((p.leaf, label_of(p)))
    return out


def label_of(p: t.Pick) -> str:
    if p.role is None:
        return UNUSED
    for label, role, depth in CHOICES:
        if role == p.role and depth is not None and abs(p.depth - depth) < 1e-6:
            return label
    for label, role, depth in CHOICES:
        if role == p.role and depth is None:
            return label
    return p.role


def apply_choice(p: t.Pick, label: str) -> None:
    _, role, depth = next(c for c in CHOICES if c[0] == label)
    p.role, p.part, p.explicit, p.quiet = role, None, False, False
    p.depth = 0.0 if role is None else (t.ROLE_DEFAULTS[role][1] if depth is None else depth)
    p.how = "手動で外した" if role is None else t.HOW_MANUAL


# --- プレビュー用の小さな画像 --------------------------------------------------------


def _small_layer(layer, step: int) -> tuple[int, int, np.ndarray] | None:
    """レイヤー 1 枚(不透明度・マスク込み)を 1/step にする。位置は縮小後の座標。"""
    img = layer.topil()
    if img is None or img.width == 0 or img.height == 0:
        return None
    img = img.convert("RGBA")
    x0, y0 = layer.bbox[:2]
    alpha = img.getchannel("A")
    if layer.opacity < 255:
        alpha = alpha.point(lambda v: v * layer.opacity // 255)
    mask = getattr(layer, "mask", None)
    if mask is not None and layer.has_mask() and not getattr(mask, "disabled", False):
        try:
            m = Image.new("L", img.size, getattr(mask, "background_color", 0))
            mimg = mask.topil()
            if mimg is not None:
                m.paste(mimg.convert("L"), (mask.bbox[0] - x0, mask.bbox[1] - y0))
            alpha = ImageChops.multiply(alpha, m)
        except Exception:  # プレビューなので、マスクが読めなくても続ける
            pass
    img.putalpha(alpha)
    gx, gy = x0 // step * step, y0 // step * step  # 縮小の格子にそろえる
    w = -(-(x0 + img.width - gx) // step) * step
    h = -(-(y0 + img.height - gy) // step) * step
    canvas = Image.new("RGBA", (w, h))
    canvas.paste(img, (x0 - gx, y0 - gy))
    small = canvas.convert("RGBa").reduce(step).convert("RGBA")  # 乗算済みで縮小して、縁を暗くしない
    return gx // step, gy // step, t._to_float(small)


def _over(dst: np.ndarray, src: np.ndarray, x: int, y: int, mode: str, clip: bool = False) -> None:
    """dst の (x, y) に src を重ねる(はみ出す部分は切る)。"""
    h, w = dst.shape[:2]
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    dx1, dy1 = min(w, x + src.shape[1]), min(h, y + src.shape[0])
    if dx1 <= dx0 or dy1 <= dy0:
        return
    view = dst[dy0:dy1, dx0:dx1]
    part = src[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
    t.composite_over(view, part, mode, clip_alpha=view[..., 3:4].copy() if clip else None)


class Thumbs:
    """レイヤーごとの小さな画像(クリップ込み)。最初に 1 回だけ作る。"""

    def __init__(self, psd, items: list[t.Leaf], step: int, progress=None):
        self.step = step
        self.size = (-(-psd.width // step), -(-psd.height // step))
        self.cache: dict[int, tuple[int, int, np.ndarray] | None] = {}
        targets = [leaf for leaf in items if not t.is_clipping(leaf.layer)]
        for n, leaf in enumerate(targets):
            if progress:
                progress(n, len(targets))
            self.cache[leaf.index] = self._render(leaf)

    def _render(self, leaf: t.Leaf) -> tuple[int, int, np.ndarray] | None:
        try:
            base = _small_layer(leaf.layer, self.step)
            if base is None:
                return None
            bx, by, arr = base
            for clip in getattr(leaf.layer, "clip_layers", None) or []:
                if not clip.visible:
                    continue
                c = _small_layer(clip, self.step)
                if c is not None:
                    _over(arr, c[2], c[0] - bx, c[1] - by, t.blend_name(clip), clip=True)
            return bx, by, arr
        except Exception:  # 読めないレイヤーはプレビューに出さないだけ
            return None

    def compose(self, view: list[tuple[t.Leaf, str]], extra: list[t.Leaf], selected: set[int],
                colors: dict[str, str] | None) -> np.ndarray:
        """view(見えているレイヤーと表示名)を重ねる。extra(選んだが見えていないレイヤー)も重ねる。"""
        w, h = self.size
        out = np.zeros((h, w, 4), dtype=np.float32)
        shown = [*view, *((leaf, UNUSED) for leaf in extra)]
        for leaf, label in sorted(shown, key=lambda v: v[0].index):
            th = self.cache.get(leaf.index)
            if th is None:
                continue
            x, y, arr = th
            src = arr
            if colors is not None:
                src = arr.copy()
                rgb = np.array([int(colors.get(label, "#888888")[i : i + 2], 16) for i in (1, 3, 5)]) / 255.0
                src[..., :3] = src[..., :3] * (1 - TINT) + rgb * TINT
            if selected and colors is None and leaf.index not in selected:
                src = src.copy() if src is arr else src
                src[..., 3] *= FADE
            _over(out, src, x, y, t.blend_name(leaf.layer))
        return out

    def hit(self, view: list[tuple[t.Leaf, str]], px: int, py: int) -> t.Leaf | None:
        """プレビューの (px, py) で一番手前に見えているレイヤー。"""
        for leaf, _ in sorted(view, key=lambda v: v[0].index, reverse=True):
            th = self.cache.get(leaf.index)
            if th is None:
                continue
            x, y, arr = th
            if 0 <= py - y < arr.shape[0] and 0 <= px - x < arr.shape[1] and arr[py - y, px - x, 3] > 0.3:
                return leaf
        return None


def to_photo_data(arr: np.ndarray, size: tuple[int, int] | None = None) -> str:
    """プレビュー(背景の市松模様込み)を tk.PhotoImage に渡す PNG(base64)にする。size は表示の大きさ。"""
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.where(((yy // 8 + xx // 8) % 2)[..., None] == 0, 0.92, 0.82).astype(np.float32)
    a = arr[..., 3:4]
    rgb = arr[..., :3] * a + bg * (1 - a)
    img = Image.fromarray(np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8), "RGB")
    if size is not None and size != (w, h):
        img = img.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- 画面 ---------------------------------------------------------------------------

SPLASH = None  # 配布版で、修正画面が開くまで出す画面(packaging/psd_main.py)


def close_splash() -> None:
    global SPLASH
    if SPLASH is not None:
        try:
            SPLASH.destroy()
        except Exception:
            pass
        SPLASH = None


class _Capture:
    """書き出し中の print を覚えておく(失敗したとき画面に出す)。元の出力にもそのまま出す。"""

    def __init__(self, orig):
        self.orig, self.text = orig, []

    def write(self, s: str) -> int:
        self.text.append(s)
        if self.orig is not None:
            self.orig.write(s)
        return len(s)

    def flush(self) -> None:
        if self.orig is not None:
            self.orig.flush()

    def tail(self, n: int = 12) -> str:
        lines = [x for x in "".join(self.text).splitlines() if x.strip()]
        return "\n".join(lines[-n:])



class Editor:
    def __init__(self, psd_path: Path, psd, items: list[t.Leaf], picks: list[t.Pick], out: Path,
                 exprs: list[t.Expr] | None = None, frames: bool = False):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.psd_path, self.psd, self.items, self.out = psd_path, psd, items, out
        self.picks = sorted(picks, key=lambda p: p.leaf.index)
        self.by_index = {p.leaf.index: p for p in self.picks}
        self.leaf_by_index = {leaf.index: leaf for leaf in items}
        self.exprs: list[t.Expr] = list(exprs or [])
        self.exported = False
        self.scale = 1.0  # プレビューの表示倍率(ウィンドウの大きさに合わせる)
        self._redraw_job = None

        close_splash()
        self.root = root = tk.Tk()
        root.report_callback_exception = self._on_error
        root.title(f"FlatRig 部品の確認 - {psd_path.name}")
        max_h = max(320, min(PREVIEW_MAX_HEIGHT, root.winfo_screenheight() - 300))
        step = max(1, -(-psd.height // max_h))
        status = tk.StringVar(value="プレビューを準備しています…")
        ttk.Label(root, textvariable=status).pack(padx=12, pady=12)
        root.update()

        def progress(n: int, total: int) -> None:
            if n % 10 == 0:
                status.set(f"プレビューを準備しています… {n}/{total}")
                root.update()

        self.thumbs = Thumbs(psd, items, step, progress)
        self.frames = tk.BooleanVar(value=frames)
        self.expr_name = tk.StringVar(value=NORMAL)
        for w in root.winfo_children():
            w.destroy()
        self._build()
        self._fill_tree()
        self._refresh()

    # 画面の部品
    def _build(self) -> None:
        tk, ttk, root = self.tk, self.ttk, self.root
        header = ttk.Label(
            root,
            justify="left",
            text="自動で部品に分けました。違うところは、左の一覧か右の画像でレイヤーを選び、下の欄で部品を選び直してください。\n"
            "表情を選ぶと、その表情で使う枠(目・口・追加パーツ)を選べます。選ばなかった枠は通常の部品のままです。",
        )
        header.pack(side="top", fill="x", padx=10, pady=(10, 4))

        # 下の操作部分を先に置く(ウィンドウを縮めたとき、一覧とプレビューの方が先に縮むように)
        foot = ttk.Frame(root)
        foot.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self.summary = tk.StringVar()
        self.summary_label = ttk.Label(foot, textvariable=self.summary, justify="left")
        ttk.Button(foot, text="やめる", command=self.root.destroy).pack(side="right")
        self.export_button = ttk.Button(foot, text="書き出す", command=self._on_export)
        self.export_button.pack(side="right", padx=6)
        ttk.Button(foot, text="自動の分け方に戻す", command=self._on_reset).pack(side="right")
        ttk.Button(foot, text="左右を入れ替える", command=self._on_swap_sides).pack(side="right", padx=6)
        self.summary_label.pack(side="left", fill="x", expand=True)

        edit = ttk.Frame(root)
        edit.pack(side="bottom", fill="x", padx=10, pady=6)
        self.choice_title = tk.StringVar(value="選んだレイヤーの部品:")
        ttk.Label(edit, textvariable=self.choice_title).pack(side="left")
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(edit, textvariable=self.choice, state="disabled", width=30,
                                  height=20, values=[c[0] for c in CHOICES])
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._on_choice())
        self.clear_button = ttk.Button(edit, text="選択解除", command=self._clear_selection, state="disabled")
        self.clear_button.pack(side="left", padx=6)
        self.selection_info = tk.StringVar()
        ttk.Label(edit, textvariable=self.selection_info).pack(side="left", padx=6)

        exprbar = ttk.Frame(root)
        exprbar.pack(side="bottom", fill="x", padx=10, pady=(6, 0))
        ttk.Label(exprbar, text="表情:").pack(side="left")
        self.expr_combo = ttk.Combobox(exprbar, textvariable=self.expr_name, state="readonly", width=16)
        self.expr_combo.pack(side="left", padx=6)
        self.expr_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_expr())
        ttk.Button(exprbar, text="表情を追加", command=self._on_add_expr).pack(side="left")
        self.rename_button = ttk.Button(exprbar, text="名前を変更", command=self._on_rename_expr)
        self.rename_button.pack(side="left", padx=4)
        self.delete_button = ttk.Button(exprbar, text="削除", command=self._on_delete_expr)
        self.delete_button.pack(side="left")
        ttk.Checkbutton(exprbar, text="口は絵の差し替え(閉じ・半開き・開き)", variable=self.frames,
                        command=self._refresh).pack(side="left", padx=(16, 0))

        body = ttk.Frame(root)
        body.pack(side="top", fill="both", expand=True, padx=10)
        right = ttk.Frame(body)
        right.pack(side="right", fill="y", padx=(10, 0))
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.tree = tree = ttk.Treeview(left, columns=("part", "expr", "how"), selectmode="extended", height=8)
        tree.heading("#0", text="レイヤー(上が手前)")
        tree.heading("part", text="部品")
        tree.heading("expr", text="表情")
        tree.heading("how", text="決め方")
        tree.column("#0", width=260)
        tree.column("part", width=220)
        tree.column("expr", width=160)
        tree.column("how", width=190)
        tree.tag_configure("unused", foreground="#888888")
        tree.tag_configure("manual", foreground="#1a56c4")
        tree.tag_configure("clip", foreground="#888888")
        bar = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        tree.bind("<Button-1>", self._on_tree_click)
        tree.bind("<Double-Button-1>", lambda _e: None)  # ダブルクリック(グループの開閉)では解除しない

        # プレビューの下の切り替えと凡例も先に置き、残りをプレビューにする
        self.legend = ttk.Frame(right)
        self.legend.pack(side="bottom", fill="x")
        self.mode = tk.StringVar(value="focus")
        modes = ttk.Frame(right)
        modes.pack(side="bottom", fill="x", pady=4)
        ttk.Radiobutton(modes, text="選んだものを強調", value="focus", variable=self.mode,
                        command=self._redraw).pack(side="left")
        ttk.Radiobutton(modes, text="部品ごとに色分け", value="color", variable=self.mode,
                        command=self._redraw).pack(side="left", padx=8)
        w, h = self.thumbs.size
        self.canvas = tk.Canvas(right, width=w, height=h, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())

        root.bind("<Escape>", lambda _e: self._clear_selection())

        # 最初は全部入る大きさで開き、縮めても下の操作部分が隠れない高さまでにとどめる
        root.update_idletasks()
        min_h = (header.winfo_reqheight() + edit.winfo_reqheight() + exprbar.winfo_reqheight()
                 + foot.winfo_reqheight() + MIN_PREVIEW + 40)
        root.minsize(min(640, root.winfo_reqwidth()), min_h)
        max_w, max_h = root.winfo_screenwidth() - 40, root.winfo_screenheight() - 80
        if root.winfo_reqheight() > max_h or root.winfo_reqwidth() > max_w:
            root.geometry(f"{min(root.winfo_reqwidth(), max_w)}x{max(min_h, min(root.winfo_reqheight(), max_h))}+10+10")

    def _fill_tree(self) -> None:
        tree = self.tree
        tree.delete(*tree.get_children())
        self.group_leaves: dict[str, list[int]] = {}
        group_ids: dict[tuple[str, ...], str] = {}
        for leaf in reversed(self.items):  # 手前を上に(Photoshop と同じ)
            parent = ""
            for depth in range(len(leaf.parents)):
                key = leaf.parents[: depth + 1]
                gid = group_ids.get(key)
                if gid is None:
                    gid = group_ids[key] = f"G{len(group_ids)}"
                    tree.insert(parent, "end", iid=gid, text=leaf.parents[depth], open=depth == 0)
                if not t.is_clipping(leaf.layer):
                    self.group_leaves.setdefault(gid, []).append(leaf.index)
                parent = gid
            if t.is_clipping(leaf.layer):
                tree.insert(parent, "end", iid=f"C{leaf.index}", text=f"↳ {leaf.layer.name}",
                            values=("(下のレイヤーにクリップ)", "", ""), tags=("clip",))
            else:
                tree.insert(parent, "end", iid=f"L{leaf.index}", text=leaf.layer.name)
        self._update_rows()

    def _expr_text(self, index: int) -> str:
        return "、".join(f"{x.name}: {t.EXPR_SLOT_LABELS[s].split('(')[0]}" for x in self.exprs
                        for s in [slot_of(x, index)] if s)

    def _update_rows(self) -> None:
        for p in self.picks:
            iid = f"L{p.leaf.index}"
            expr = self._expr_text(p.leaf.index)
            tag = "unused" if p.role is None and not expr else "manual" if p.how == t.HOW_MANUAL else ""
            self.tree.item(iid, values=(label_of(p), expr, p.how), tags=(tag,) if tag else ())
        for gid, indices in self.group_leaves.items():
            labels = {label_of(self.by_index[i]) for i in indices if i in self.by_index}
            self.tree.set(gid, "part", labels.pop() if len(labels) == 1 else MIXED)
            exprs = {self._expr_text(i) for i in indices if i in self.by_index}
            self.tree.set(gid, "expr", exprs.pop() if len(exprs) == 1 else MIXED)

    def _current(self) -> t.Expr | None:
        name = self.expr_name.get()
        return next((x for x in self.exprs if x.name == name), None)

    def _selected_indices(self) -> list[int]:
        out: list[int] = []
        for iid in self.tree.selection():
            if iid.startswith("L"):
                out.append(int(iid[1:]))
            elif iid.startswith("G"):
                out += self.group_leaves.get(iid, [])
        return [i for i in dict.fromkeys(out) if i in self.by_index]

    # 操作
    def _on_select(self) -> None:
        sel = self._selected_indices()
        if not sel:
            self.combo.configure(state="disabled")
            self.choice.set("")
            self.selection_info.set("")
            self.clear_button.configure(state="disabled")
        else:
            x = self._current()
            if x is None:
                labels = {label_of(self.by_index[i]) for i in sel}
            else:
                labels = {next(c[0] for c in SLOT_CHOICES if c[1] == slot_of(x, i)) for i in sel}
            self.combo.configure(state="readonly")
            self.choice.set(labels.pop() if len(labels) == 1 else "")
            self.selection_info.set(f"{len(sel)} 枚を選択中")
            self.clear_button.configure(state="normal")
        self._redraw()

    def _on_choice(self) -> None:
        label = self.choice.get()
        x = self._current()
        if x is not None:
            slot = next(c[1] for c in SLOT_CHOICES if c[0] == label)
            indices = self._selected_indices()
            set_slot(x, indices, slot)
            for i in indices if slot else []:
                p = self.by_index[i]
                if p.role is not None:  # 表情の部品は通常の見た目には出さない
                    apply_choice(p, UNUSED)
                p.how = f"表情「{x.name}」で使う"
        else:
            for i in self._selected_indices():
                apply_choice(self.by_index[i], label)
            if label == next(c[0] for c in CHOICES if c[1] == "mouth_half"):
                self.frames.set(True)  # 半開きの口は絵の差し替えのときだけ使う
        self._refresh()

    # 表情
    def _expr_names(self) -> list[str]:
        return [NORMAL, *(x.name for x in self.exprs)]

    def _on_expr(self) -> None:
        x = self._current()
        if x is None:
            self.choice_title.set("選んだレイヤーの部品:")
            self.combo.configure(values=[c[0] for c in CHOICES])
        else:
            self.choice_title.set(f"表情「{x.name}」で使う枠:")
            self.combo.configure(values=[c[0] for c in SLOT_CHOICES])
        self._refresh()

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        from tkinter import messagebox, simpledialog

        name = simpledialog.askstring("FlatRig", title, initialvalue=initial, parent=self.root)
        if name is None:
            return None
        name = name.strip()
        if not name or name == NORMAL or (name != initial and name in self._expr_names()):
            messagebox.showerror("FlatRig", "空欄・「通常」・ほかの表情と同じ名前は使えません。別の名前にしてください。", parent=self.root)
            return None
        return name

    def _on_add_expr(self) -> None:
        from tkinter import messagebox

        if len(self.exprs) >= t.MAX_EXPRESSIONS:
            messagebox.showerror("FlatRig", f"表情は {t.MAX_EXPRESSIONS} 個まで追加できます。", parent=self.root)
            return
        name = self._ask_name("新しい表情の名前(例: 笑顔、怒り)")
        if name:
            self.exprs.append(t.Expr(name))
            self.expr_name.set(name)
            self._on_expr()

    def _on_rename_expr(self) -> None:
        x = self._current()
        if x is None:
            return
        name = self._ask_name("表情の新しい名前", x.name)
        if name:
            x.name = name
            self.expr_name.set(name)
            self._refresh()

    def _on_delete_expr(self) -> None:
        from tkinter import messagebox

        x = self._current()
        if x is None or not messagebox.askyesno("FlatRig", f"表情「{x.name}」を削除しますか？", parent=self.root):
            return
        self.exprs.remove(x)
        self.expr_name.set(NORMAL)
        self._on_expr()

    def _clear_selection(self) -> None:
        """選ぶのをやめて、プレビューを全体の表示に戻す。"""
        if self.tree.selection():
            self.tree.selection_set(())

    def _on_tree_click(self, event):
        """一覧の空いている所や、選んでいる 1 行をもう一度クリックしたら選択を解除する。"""
        if event.state & 0x0005:  # Shift / Ctrl を押しながらは標準の動き(複数選択)
            return None
        region = self.tree.identify_region(event.x, event.y)
        if region in ("heading", "separator"):
            return None
        row = self.tree.identify_row(event.y)
        if not row:
            self._clear_selection()
            return "break"
        if "indicator" in self.tree.identify_element(event.x, event.y):  # グループの開閉
            return None
        if self.tree.selection() == (row,):
            self._clear_selection()
            self.tree.focus(row)
            return "break"
        return None

    def _on_click(self, event) -> None:
        if self.scale <= 0:
            return
        leaf = self.thumbs.hit(self._view(), int(event.x / self.scale), int(event.y / self.scale))
        if leaf is None:
            self._clear_selection()
            return
        iid = f"L{leaf.index}"
        if self.tree.selection() == (iid,):  # 選んでいるものをもう一度クリックしたら解除
            self._clear_selection()
            return
        self.tree.selection_set(iid)
        self.tree.see(iid)

    def _on_reset(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("FlatRig", "手で直した部品分けと表情を消して、自動の分け方に戻しますか？", parent=self.root):
            return
        self.picks = t.classify(self.psd, self.items)
        exprs, frames = t.auto_expressions(self.psd, self.items, self.picks)
        self.picks.sort(key=lambda p: p.leaf.index)
        self.by_index = {p.leaf.index: p for p in self.picks}
        self.exprs = exprs
        self.frames.set(frames)
        self.expr_name.set(NORMAL)
        self._on_expr()

    def _on_swap_sides(self) -> None:
        from tkinter import messagebox

        if swap_sides(self.picks, self.exprs) == 0:
            messagebox.showinfo("FlatRig", "左右を入れ替えられる部品がありません。", parent=self.root)
            return
        self._refresh()

    def _on_export(self) -> None:
        import json
        from tkinter import messagebox

        m = self._mapping()
        self.out.mkdir(parents=True, exist_ok=True)
        mapping_path = self.out / "mapping.json"
        mapping_path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.root.configure(cursor="watch")
        self.summary.set("書き出しています…(しばらくかかります)")
        self.root.update()
        import contextlib
        import sys

        cap = _Capture(sys.stdout)
        with contextlib.redirect_stdout(cap):
            try:
                code = t.export_mapping(m, self.out, self.out / "layers")
            except Exception as exc:
                code = 1
                print(f"エラー: {exc}")
        self.root.configure(cursor="")
        if code == 0:
            self.exported = True
            text = (f"書き出しました。\n{self.out / 'layers'}\n\n"
                    "Unity の FlatRig ウィンドウで、このフォルダの layers.json を選んで「アバターを生成」を押してください。")
            messagebox.showinfo("FlatRig", text, parent=self.root)
            self.root.destroy()
        else:
            text = "書き出せませんでした。\n\n" + (cap.tail() or "(原因を特定できませんでした)")
            messagebox.showerror("FlatRig", text, parent=self.root)
            self._refresh()

    # 表示の更新
    def _mapping(self) -> dict:
        return t.build_mapping(self.psd_path, self.psd, self.picks, self.exprs, self.frames.get())

    def _view(self) -> list[tuple[t.Leaf, str]]:
        return expr_view(self.picks, self.exprs, self._current(), self.frames.get())

    def _refresh(self) -> None:
        self.expr_combo.configure(values=self._expr_names())
        if self.expr_name.get() not in self._expr_names():
            self.expr_name.set(NORMAL)
        state = "disabled" if self._current() is None else "normal"
        self.rename_button.configure(state=state)
        self.delete_button.configure(state=state)
        self._update_rows()
        m = self._mapping()
        roles = {e["role"] for e in m["layers"]}
        missing = [t.ROLE_LABELS[r] for r in t.RECOMMENDED_ROLES if r not in roles]
        n_expr = len(m.get("expressions", []))
        lines = [f"部品 {len(m['layers'])} 個 / 表情 {n_expr} 個 / 使わないレイヤー {len(m['unused'])} 枚"]
        if missing:
            lines.append("足りない部品(この動きは付きません): " + "、".join(missing))
        empty = [x.name for x in self.exprs if not x.slots]
        if empty:
            lines.append("レイヤーが入っていない表情は書き出しません: " + "、".join(empty))
        if not self.frames.get() and "mouth_half" in roles:
            lines.append("半開きの口は「口は絵の差し替え」を ON にしたときだけ使います。")
        if "face" not in roles:
            lines.append("顔の部品が無いと書き出せません。顔のレイヤーを「顔」にしてください。")
        self.summary.set("\n".join(lines))
        self.export_button.configure(state="normal" if "face" in roles else "disabled")
        self._on_select()

    def _colors(self, view: list[tuple[t.Leaf, str]]) -> dict[str, str]:
        labels = list(dict.fromkeys(label for _, label in view))
        return {label: PALETTE[i % len(PALETTE)] for i, label in enumerate(labels)}

    def _schedule_redraw(self) -> None:
        """ウィンドウの大きさを変えている間は描き直しをまとめる。"""
        if self._redraw_job is not None:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(80, self._redraw)

    def _fit_size(self) -> tuple[int, int]:
        """プレビューの欄に収まる表示の大きさ(元より大きくはしない)を決め、倍率を覚える。"""
        w, h = self.thumbs.size
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:  # まだ画面に出ていない
            cw, ch = w, h
        self.scale = min(1.0, cw / w, ch / h)
        return max(1, round(w * self.scale)), max(1, round(h * self.scale))

    def _redraw(self) -> None:
        self._redraw_job = None
        view = self._view()
        colors = self._colors(view) if self.mode.get() == "color" else None
        selected = set(self._selected_indices())
        in_view = {leaf.index for leaf, _ in view}
        extra = [self.leaf_by_index[i] for i in sorted(selected - in_view)]  # 選んだものは隠れていても出す
        arr = self.thumbs.compose(view, extra, selected, colors)
        size = self._fit_size()
        self.photo = self.tk.PhotoImage(data=to_photo_data(arr, size))  # 参照を持っておかないと消える
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        for w in self.legend.winfo_children():
            w.destroy()
        if colors:
            for n, (label, color) in enumerate(colors.items()):
                row = self.ttk.Frame(self.legend)
                row.grid(row=n // 2, column=n % 2, sticky="w", padx=(0, 8))
                self.tk.Label(row, bg=color, width=2).pack(side="left")
                self.ttk.Label(row, text=label).pack(side="left", padx=2)

    def _on_error(self, exc, val, tb) -> None:
        """画面の操作中の思わぬエラー。黒い画面が無いので、記録に残してから画面で知らせる。"""
        import traceback
        from tkinter import messagebox

        traceback.print_exception(exc, val, tb)
        messagebox.showerror("FlatRig", f"予期しないエラーが起きました。\n{val}", parent=self.root)

    def run(self) -> bool:
        self.root.mainloop()
        return self.exported


def run(psd_path: Path, psd, items: list[t.Leaf], picks: list[t.Pick], out: Path,
        exprs: list[t.Expr] | None = None, frames: bool = False) -> bool:
    """修正画面を開く。exprs は表情、frames は口が絵の差し替えか。書き出したら True。"""
    return Editor(psd_path, psd, items, picks, out, exprs, frames).run()
