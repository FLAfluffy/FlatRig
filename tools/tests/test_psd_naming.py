"""psd_to_layers の自動の対応表(PSD の読み込みは不要。レイヤーの木を偽物で作る)。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psd_to_layers as t  # noqa: E402


class FakeLayer:
    def __init__(self, name, children=None, visible=True, clipping=False, bbox=(0, 0, 10, 10)):
        self.name, self.visible, self.clipping = name, visible, clipping
        self.children = children
        self.bbox = bbox

    def is_group(self):
        return self.children is not None

    def __iter__(self):
        return iter(self.children or [])


class FakePsd(list):
    width, height = 3000, 7800


def auto(psd):
    return t.auto_mapping(Path("x.psd"), psd, t.leaves(psd))


def test_parse_part_name():
    assert t.parse_part_name("face") == ("face", "face", None)
    assert t.parse_part_name("eye_l_white") == ("eye_l_white", "eye_l_white", None)
    assert t.parse_part_name("Back_Hair.ribbon @ -0.8") == ("back_hair", "Back_Hair.ribbon", -0.8)
    assert t.parse_part_name("face_parts@0.2") == ("face", "face_parts", 0.2)
    assert t.parse_part_name("back_ribbon") is None
    assert t.parse_part_name("faces") is None
    assert t.parse_part_name("顔") is None


def test_convention_names_order_and_merge():
    psd = FakePsd([
        FakeLayer("_下書き"),
        FakeLayer("back_hair.ribbon@-0.8"),
        FakeLayer("back_hair", [FakeLayer("髪1"), FakeLayer("_メモ"), FakeLayer("髪2")]),
        FakeLayer("body"),
        FakeLayer("頭", [
            FakeLayer("face"),
            FakeLayer("影", clipping=True),
            FakeLayer("mouth_open", [FakeLayer("口内")]),
            FakeLayer("mouth_open.舌"),
            FakeLayer("eye_l_white", visible=False),
        ]),
    ])
    m, unused = auto(psd)
    names = [e["name"] for e in m["layers"]]
    assert names == ["back_hair.ribbon", "back_hair", "body", "face", "mouth_open"]
    by = {e["name"]: e for e in m["layers"]}
    assert by["back_hair.ribbon"]["depth"] == -0.8
    assert by["back_hair"]["sources"] == ["back_hair/髪1", "back_hair/髪2"]
    assert by["mouth_open"]["sources"] == ["頭/mouth_open/口内", "頭/mouth_open.舌"]  # 機能部品は 1 つにまとめる
    assert by["body"]["group"] == "body"
    assert unused == ["頭/eye_l_white(非表示)"]
    assert m["scale"] == 1 / 3  # 7800px → 2600px
    assert "head_pivot" not in m  # 命名規則の face があれば Unity の既定
    assert not t.validate_mapping(m)


def test_auto_from_japanese_names_and_position():
    L = FakeLayer
    psd = FakePsd([
        L("しっぽ", bbox=(1200, 3000, 1800, 5000)),               # 名前で分からない・顔の外 → 体
        L("後ろ髪", [L("髪", bbox=(800, 500, 2200, 4000))]),
        L("体", [L("腕", bbox=(900, 1900, 2100, 3000)), L("スカート", bbox=(900, 3000, 2100, 4000))]),
        L("顔", [
            L("輪郭", bbox=(1100, 800, 1900, 1800)),
            L("前髪の影", bbox=(1150, 850, 1850, 1200)),          # 顔に挟まれている → 顔
            L("顔線", bbox=(1100, 800, 1900, 1800)),
        ]),
        L("前髪", bbox=(1000, 600, 2000, 1300)),
        L("口", [L("口内", bbox=(1400, 1550, 1600, 1650)), L("下唇", bbox=(1400, 1560, 1600, 1660)),
                L("上唇", bbox=(1400, 1550, 1600, 1580))]),
        L("目", [L("白目", bbox=(1200, 1200, 1400, 1300)), L("白目", bbox=(1600, 1200, 1800, 1300)),
                L("まつ毛", bbox=(1200, 1180, 1400, 1300)), L("まつ毛", bbox=(1600, 1180, 1800, 1300))]),
        L("ヘッドセット", bbox=(1700, 1300, 1900, 1700)),          # 顔の範囲・顔より手前 → 顔の飾り
        L("レイヤー 1", bbox=(1450, 1560, 1550, 1640)),           # 口の範囲・付けたままの名前 → 差分
        L("表情差分", [L("笑い", bbox=(1400, 1550, 1600, 1650))]),
        L("透かし", bbox=(0, 0, 3000, 7800)),                     # 消さない
    ])
    m, unused = auto(psd)
    got = [(e["name"], e["role"], e["depth"]) for e in m["layers"]]
    assert got == [
        ("body", "body", 0.0), ("back_hair", "back_hair", -0.6), ("body_2", "body", 0.0), ("body_3", "body", 0.0),
        ("face", "face", 0.0), ("face_2", "face", 0.0), ("face_3", "face", 0.0), ("front_hair", "front_hair", 0.6),
        ("mouth_open", "mouth_open", 0.15), ("mouth_closed", "mouth_closed", 0.16),
        ("eye_l_white", "eye_l_white", 0.2), ("eye_r_white", "eye_r_white", 0.2),
        ("eye_l_lash", "eye_l_lash", 0.22), ("eye_r_lash", "eye_r_lash", 0.22),
        ("face_4", "face", 0.1), ("body_4", "body", 0.0),
    ]
    by = {e["name"]: e for e in m["layers"]}
    # 自動の部品は PSD のレイヤーごと(Unity の表示順で並べ替えられるように)。顔に挟まれた前髪の影は顔の役割
    assert [by[n]["sources"] for n in ("face", "face_2", "face_3")] == [["顔/輪郭"], ["顔/前髪の影"], ["顔/顔線"]]
    assert by["mouth_open"]["sources"] == ["口/口内", "口/下唇"]
    assert by["body_4"]["sources"] == ["透かし"]
    assert m["head_pivot"] == [1500, 1800]
    assert unused == [
        "レイヤー 1(目・口の範囲にある名前の分からないレイヤーなので、差分と判断)",
        "表情差分/笑い(差分・ガイドと判断)",
    ]
    assert not t.validate_mapping(m)


def test_glob_escape():
    assert t._glob_escape("a[1]*?") == "a[[]1][*][?]"


def test_mapping_roundtrip_and_manual_choice():
    import psd_editor as e

    L = FakeLayer
    psd = FakePsd([
        L("後ろ髪", bbox=(800, 500, 2200, 4000)),
        L("体", bbox=(900, 1900, 2100, 4000)),
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("リボン", bbox=(1300, 700, 1700, 900)),  # 顔の範囲・顔より手前 → 顔の飾り
        L("口", [L("上唇", bbox=(1400, 1550, 1600, 1580)), L("下唇", bbox=(1400, 1560, 1600, 1660))]),
    ])
    items = t.leaves(psd)
    picks = t.classify(psd, items)
    assert [e.label_of(p) for p in picks] == ["後ろ髪", "体", "顔", "顔の飾り(顔より手前)",
                                             "閉じた口(上唇・唇の線・隠し)", "開いた口(口の中・歯・舌・下唇)"]
    m = t.build_mapping(Path("x.psd"), psd, picks)

    # 保存した対応表から戻すと、同じ対応表になる
    back = t.picks_from_mapping(m, items)
    assert t.build_mapping(Path("x.psd"), psd, back)["layers"] == m["layers"]

    # 手で直す: 下唇も閉じた口に、リボンは使わない
    e.apply_choice(back[5], "閉じた口(上唇・唇の線・隠し)")
    e.apply_choice(back[3], e.UNUSED)
    m2 = t.build_mapping(Path("x.psd"), psd, back)
    by = {x["name"]: x for x in m2["layers"]}
    assert by["mouth_closed"]["sources"] == ["口/上唇", "口/下唇"]
    assert "mouth_open" not in by and "face_2" not in by
    assert m2["unused"] == ["リボン(手動で外した)"]
    assert not t.validate_mapping(m2)


def test_preview_paste_clips_to_canvas():
    import numpy as np
    import psd_editor as e

    dst = np.zeros((4, 4, 4), dtype=np.float32)
    src = np.ones((3, 3, 4), dtype=np.float32)
    e._over(dst, src, -1, 2, "NORMAL")  # 左下にはみ出す
    assert dst[..., 3].sum() == 4  # (x 0〜1, y 2〜3) の 4 画素だけ
    assert dst[2:4, 0:2, 3].min() == 1


def test_mouth_cover_structure():
    """唇線と隠しで閉じた口を作り、隠しを消すと最大まで開いた口内が見える構造。"""
    L = FakeLayer
    psd = FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("口のまとめ", [
            L("口内", bbox=(1400, 1550, 1600, 1650)), L("舌", bbox=(1410, 1590, 1590, 1640)),
            L("口内の影", bbox=(1380, 1500, 1620, 1620)),
            L("下唇隠し", bbox=(1395, 1580, 1605, 1660)), L("下唇線", bbox=(1450, 1600, 1550, 1610)),
            L("上唇隠し", bbox=(1395, 1540, 1605, 1610)), L("上唇線", bbox=(1450, 1605, 1550, 1615)),
        ]),
    ])
    m, _ = auto(psd)
    by = {e["name"]: e for e in m["layers"]}
    assert by["mouth_open"]["sources"] == ["口のまとめ/口内", "口のまとめ/舌", "口のまとめ/口内の影"]
    assert by["mouth_closed"]["sources"] == [
        "口のまとめ/下唇隠し", "口のまとめ/下唇線", "口のまとめ/上唇隠し", "口のまとめ/上唇線"]
    assert by["mouth_closed"]["hide_when_open"] is True
    assert "hide_when_open" not in by["mouth_open"]
    assert not t.validate_mapping(m)


def test_mouth_without_cover_keeps_lower_lip_open():
    L = FakeLayer
    psd = FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("口", [L("口内", bbox=(1400, 1550, 1600, 1650)), L("下唇線", bbox=(1400, 1640, 1600, 1650)),
                L("上唇", bbox=(1400, 1550, 1600, 1580))]),
    ])
    m, _ = auto(psd)
    by = {e["name"]: e for e in m["layers"]}
    assert by["mouth_open"]["sources"] == ["口/口内", "口/下唇線"]
    assert "hide_when_open" not in by["mouth_closed"]


def test_guess_eye_shadow_and_pupil_names():
    # 白目の上部の影は独立した役割(まつ毛と一緒に下がる)。口内の影・髪の影は対象外
    assert t.guess(("右目の影",)) == "eye_?_shadow"
    assert t.guess(("eye white shadow",)) == "eye_?_shadow"
    assert t.guess(("口内の影",)) == "mouth_open"
    assert t.guess(("右目玉",)) == "eye_?_pupil"
    assert t.guess(("鼻ハイライト",)) == "nose"
    assert t.guess(("左ハイライト",)) == "eye_?_highlight"
    assert t.guess(("eye highlight",)) == "eye_?_highlight"
    assert t.guess(("頬ハイライト",)) == "face"


def _frames_and_expressions_psd():
    """口は 閉じ・半開き・開き の絵、目は通常のまばたきの絵と表情の絵を持つ PSD。"""
    L = FakeLayer
    return FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("口のまとめ", [
            L("笑顔の口閉じ", bbox=(1400, 1550, 1600, 1580)),
            L("笑顔の口半開き", visible=False, bbox=(1400, 1550, 1600, 1620)),
            L("笑顔の口開き", visible=False, bbox=(1400, 1550, 1600, 1650)),
            L("尖った口閉じ", visible=False, bbox=(1420, 1550, 1580, 1580)),
            L("尖った口開き", visible=False, bbox=(1420, 1550, 1580, 1650)),
        ]),
        L("左目まとめ", [
            L("左白目", bbox=(1200, 1100, 1400, 1200)),
            L("左目玉", bbox=(1250, 1100, 1350, 1200)),
            L("左まぶた", bbox=(1200, 1080, 1400, 1150)),
            L("閉じた左まぶた", visible=False, bbox=(1200, 1150, 1400, 1190)),
            L("閉じた笑顔左まぶた", visible=False, bbox=(1200, 1140, 1400, 1190)),
            L("そのまま閉じた左まぶた", visible=False, bbox=(1200, 1150, 1400, 1190)),
        ]),
        L("右目まとめ", [
            L("右白目", bbox=(1600, 1100, 1800, 1200)),
            L("右まぶた", bbox=(1600, 1080, 1800, 1150)),
            L("閉じた右まぶた", visible=False, bbox=(1600, 1150, 1800, 1190)),
            L("閉じた笑顔右まぶた", visible=False, bbox=(1600, 1140, 1800, 1190)),
        ]),
    ])


def test_mouth_frames_closed_eyes_and_expressions_from_names():
    m, unused = auto(_frames_and_expressions_psd())
    by = {e["name"]: e for e in m["layers"]}
    assert m["mouth_frames"] is True
    assert by["mouth_closed"]["sources"] == ["口のまとめ/笑顔の口閉じ"]  # 表示されている口の組が通常
    assert by["mouth_half"]["sources"] == ["口のまとめ/笑顔の口半開き"]
    assert by["mouth_open"]["sources"] == ["口のまとめ/笑顔の口開き"]
    assert by["mouth_open"]["include_hidden"] is True
    assert by["eye_l_closed"]["sources"] == ["左目まとめ/閉じた左まぶた"]  # 名前の無い閉じた目はまばたき
    assert by["eye_r_closed"]["sources"] == ["右目まとめ/閉じた右まぶた"]
    assert "hide_when_open" not in by["mouth_closed"]
    assert m["expressions"] == [
        {"name": "尖った", "parts": {"mouth_closed": ["口のまとめ/尖った口閉じ"], "mouth_open": ["口のまとめ/尖った口開き"]}},
        {"name": "笑顔", "parts": {"eye_l": ["左目まとめ/閉じた笑顔左まぶた"], "eye_r": ["右目まとめ/閉じた笑顔右まぶた"]}},
    ]
    # 名前の分からない閉じた目(「そのまま」)は自動では割り当てず、確認画面で表情に入れる
    assert unused == ["左目まとめ/そのまま閉じた左まぶた(表情用の目(確認画面で表情に割り当てられます))"]
    assert not t.validate_mapping(m)


def test_expression_entries_placed_next_to_normal_parts():
    """表情の部品は入れ替える通常の部品の直後に重ねる。役割は expr_<枠>、番号は 1 から。"""
    m, _ = auto(_frames_and_expressions_psd())
    entries = t.expression_entries(m)
    names = [e["name"] for e in entries]
    assert names.index("expr1_mouth_closed") > names.index("mouth_open")
    assert names.index("expr2_eye_l") == max(i for i, n in enumerate(names) if n.startswith("eye_l_")) + 1
    e = next(e for e in entries if e["name"] == "expr2_eye_r")
    assert (e["role"], e["expr"], e["include_hidden"]) == ("expr_eye_r", 2, True)


def test_expression_group_convention_and_legacy_smile_roles():
    L = FakeLayer
    psd = FakePsd([
        L("face", bbox=(1100, 800, 1900, 1800)),
        L("eye_l_white", bbox=(1200, 1100, 1400, 1200)), L("eye_r_white", bbox=(1600, 1100, 1800, 1200)),
        L("mouth_closed", bbox=(1400, 1550, 1600, 1580)), L("mouth_open", bbox=(1400, 1550, 1600, 1650)),
        L("表情:怒り", [
            L("目", [L("a", bbox=(1200, 1100, 1400, 1200)), L("b", bbox=(1600, 1100, 1800, 1200))]),
            L("口", bbox=(1400, 1550, 1600, 1580)),
            L("怒りマーク", bbox=(1700, 800, 1800, 900)),
        ], visible=False),
        L("差分", [L("mouth_smile", bbox=(1400, 1540, 1600, 1590)), L("smile_extra", bbox=(1150, 1300, 1850, 1450))],
          visible=False),
    ])
    m, unused = auto(psd)
    ex = {x["name"]: x["parts"] for x in m["expressions"]}
    assert ex["怒り"] == {"eye_l": ["表情:怒り/目/a"], "eye_r": ["表情:怒り/目/b"],
                         "mouth_closed": ["表情:怒り/口"], "extra": ["表情:怒り/怒りマーク"]}
    assert ex["笑顔"] == {"mouth_closed": ["差分/mouth_smile"], "extra": ["差分/smile_extra"]}  # 前の版の役割名
    assert "mouth_frames" not in m  # 口の差し替えの絵が無い → 今までどおり伸び縮み
    assert not unused
    assert not t.validate_mapping(m)


def test_expressions_roundtrip_and_validation():
    psd = _frames_and_expressions_psd()
    items = t.leaves(psd)
    m, _ = auto(psd)
    exprs, frames = t.expressions_from_mapping(m, items)
    assert frames is True
    by_index = {leaf.index: leaf for leaf in items}
    assert [x.to_json(by_index) for x in exprs] == m["expressions"]
    bad = dict(m, expressions=[{"name": "a", "parts": {"nose": ["x"]}}, {"name": "a", "parts": {}}])
    errors = t.validate_mapping(bad)
    assert any("nose" in e for e in errors) and any("重複" in e for e in errors)
    many = dict(m, expressions=[{"name": str(i), "parts": {"extra": ["x"]}} for i in range(t.MAX_EXPRESSIONS + 1)])
    assert any("個まで" in e for e in t.validate_mapping(many))


def test_match_sources_uses_hidden_layers_with_include_hidden():
    L = FakeLayer
    psd = FakePsd([L("差分", [L("口開き")], visible=False)])
    items = t.leaves(psd)
    entry = {"name": "x", "sources": ["差分/口開き"], "include_hidden": True}
    assert [leaf.path for leaf in t.match_sources(entry, items, [])] == ["差分/口開き"]


def test_limb_roles_by_name_and_side():
    """手足の部品: 名前で部品を、位置で左右(画面の左が l)を決める。両腕が 1 枚なら曲げられないので体。"""
    L = FakeLayer
    psd = FakePsd([
        L("顔", [L("輪郭", bbox=(1100, 800, 1900, 1800))]),
        L("左腕", [L("上腕", bbox=(700, 1900, 900, 2600)), L("前腕", bbox=(650, 2550, 850, 3200)),
                  L("手", bbox=(640, 3150, 820, 3400))]),
        L("右腕", [L("二の腕", bbox=(2100, 1900, 2300, 2600)), L("袖", bbox=(2050, 1900, 2350, 2300))]),
        L("脚", [L("太もも", bbox=(1200, 3500, 1450, 5000)), L("すね", bbox=(1200, 5000, 1450, 6800)),
                L("靴", bbox=(1150, 6800, 1500, 7100)), L("右脚", bbox=(1550, 3500, 1800, 7100))]),
        L("腕(両方)", bbox=(600, 1900, 2400, 3000)),
    ])
    m, _ = auto(psd)
    got = [(e["name"], e["role"], e["group"]) for e in m["layers"]]
    assert got == [
        ("face", "face", "head"),
        ("arm_l_upper", "arm_l_upper", "body"), ("arm_l_lower", "arm_l_lower", "body"), ("hand_l", "hand_l", "body"),
        ("arm_r_upper", "arm_r_upper", "body"),
        ("arm_r_upper_2", "arm_r_upper", "body"),  # 袖は親グループ「右腕」から上腕(レイヤーごとに別の部品)
        ("leg_l_upper", "leg_l_upper", "body"), ("leg_l_lower", "leg_l_lower", "body"), ("foot_l", "foot_l", "body"),
        ("leg_r_upper", "leg_r_upper", "body"),  # 脚 1 枚 → 太もも(股関節で回る)
        ("body", "body", "body"),  # 両腕が 1 枚
    ]
    assert not t.validate_mapping(m)


def test_limb_role_names():
    assert t.parse_part_name("arm_l_upper") == ("arm_l_upper", "arm_l_upper", None)
    assert t.parse_part_name("hand_r.glove") == ("hand_r", "hand_r.glove", None)
    assert t.ROLE_DEFAULTS["foot_l"] == ("body", 0.0)
    assert set(t.LIMB_ROLES) == {f"{a}_{s}_{p}" for a in ("arm", "leg") for s in "lr" for p in ("upper", "lower")} | {
        "hand_l", "hand_r", "foot_l", "foot_r"}


def test_guess_varied_naming_styles():
    """日本語の言い回し・英語(区切り・キャメルケース)・略語・中国語の名前。"""
    cases = {
        ("HairFront",): "front_hair", ("Hair_Back",): "back_hair", ("Bangs",): "front_hair",
        ("SideHair",): "side_hair", ("Side", "Hair"): "side_hair", ("サイドリボン",): None,
        ("帽子",): "hair", ("Hat",): "hair",
        ("White", "Eye_L"): "eye_?_white", ("Iris_L",): "eye_?_pupil", ("右瞳hi1",): "eye_?_highlight",
        ("左反射光", "左瞳"): "eye_?_highlight", ("インク瓶hi",): None, ("Eyebrow_L",): "brow_?",
        ("リップハイライト", "口閉じ"): "mouth_closed", ("ハイライト", "前髪"): "front_hair",
        ("MouthOpen",): "mouth_open", ("Teeth",): "mouth_open", ("UpperLip",): "mouth_closed",
        ("ほほ線",): "face", ("フェイスライン",): "face", ("Head",): "face",
        ("UpperArm_L",): "arm_?_upper", ("Forearm.R",): "arm_?_lower", ("LeftArm",): "arm_?", ("Charm",): None,
        ("袖",): "arm_?", ("腕の肌",): "arm_?", ("Thigh_L",): "leg_?_upper", ("Shin",): "leg_?_lower",
        ("小腿",): "leg_?_lower", ("ひざ",): "leg_?_joint", ("Knee",): "leg_?_joint", ("Leg_R",): "leg_?",
        ("Legend",): None, ("靴下",): "foot_?", ("Shoe",): "foot_?",
        ("ボディ表",): "body", ("Torso",): "body", ("Ponytail",): "hair", ("Tail",): "body",
        ("刘海",): "front_hair", ("眼白",): "eye_?_white",
    }
    for names, kind in cases.items():
        assert t.guess(names) == kind, names


def test_parse_part_name_case_separators_and_camel_case():
    assert t.parse_part_name("BackHair") == ("back_hair", "BackHair", None)
    assert t.parse_part_name("EyeLWhite") == ("eye_l_white", "EyeLWhite", None)
    assert t.parse_part_name("arm-l-upper") == ("arm_l_upper", "arm-l-upper", None)
    assert t.parse_part_name("mouth open") == ("mouth_open", "mouth open", None)
    assert t.parse_part_name("Hand_R.Glove@0.1") == ("hand_r", "Hand_R.Glove", 0.1)
    assert t.parse_part_name("Bodyshadow") is None


class PixelLayer(FakeLayer):
    """画素(アルファ)を持つ偽のレイヤー。columns は不透明にする x の範囲(キャンバス座標)。"""

    def __init__(self, name, bbox, columns):
        super().__init__(name, bbox=bbox)
        self.columns = columns

    def topil(self):
        from PIL import Image
        x0, y0, x1, y1 = self.bbox
        img = Image.new("RGBA", (x1 - x0, y1 - y0))
        for a, b in self.columns:
            img.paste((0, 0, 0, 255), (a - x0, 0, b - x0, y1 - y0))
        return img


def test_limb_segments_sleeves_knees_and_sides():
    """袖は重なる部位へ、膝は太ももに収まれば太もも・はみ出せばすね。はみ出した影があっても片側の脚は脚。"""
    L = FakeLayer
    psd = FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("左腕のまとめ", [L("左二の腕", bbox=(700, 1900, 900, 2600)), L("左前腕", bbox=(600, 2550, 850, 3200)),
                      L("左袖の裏", bbox=(620, 3000, 800, 3200)), L("左手", bbox=(550, 3150, 800, 3400))]),
        L("下半身まとめ", [L("左太もも", bbox=(1100, 3500, 1450, 4500)), L("左ひざ", bbox=(1100, 4300, 1450, 5600)),
                       L("左足先", bbox=(1100, 5500, 1450, 5900))]),
        L("脚R", [PixelLayer("脚R", (1300, 3500, 2000, 7000), [(1550, 2000)]),  # 影が中央をまたぐ(画素は右)
                 L("ひざR", bbox=(1550, 4800, 1900, 5200)), L("靴R", bbox=(1550, 6800, 1950, 7100))]),
    ])
    m, _ = auto(psd)
    got: dict[str, list[str]] = {}
    for e in m["layers"]:
        got[e["role"]] = got.get(e["role"], []) + e["sources"]
    assert got["arm_l_lower"] == ["左腕のまとめ/左前腕", "左腕のまとめ/左袖の裏"]
    assert got["leg_l_lower"] == ["下半身まとめ/左ひざ"]
    assert got["leg_r_upper"] == ["脚R/脚R", "脚R/ひざR"]
    assert got["foot_r"] == ["脚R/靴R"]
    assert "body" not in got
    assert not t.validate_mapping(m)


def test_hair_behind_face_hats_background_and_eye_highlights():
    L = FakeLayer
    psd = FakePsd([
        L("用紙", bbox=(0, 0, 3000, 7800)),
        L("背景(インポート時削除)", bbox=(0, 0, 3000, 7800)),
        L("横髪まとめ", [L("左横髪裏", bbox=(1000, 900, 1150, 2000))]),  # 顔より奥の横髪 → 後ろ髪
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("口閉じ", [L("口閉じ線上", bbox=(1400, 1550, 1600, 1580)), L("リップハイライト", bbox=(1480, 1560, 1520, 1570))]),
        L("右目", [L("右白目", bbox=(1200, 1200, 1400, 1300)), L("右瞳", [L("右瞳ba", bbox=(1250, 1200, 1350, 1300)),
                                                                   L("右瞳hi1", bbox=(1260, 1210, 1280, 1230))])]),
        L("しっぽと後系", [L("しっぽ", bbox=(1300, 3000, 1700, 5000))]),
        L("帽子", bbox=(1000, 500, 2000, 1000)),  # 顔より手前 → 前髪と同じ
    ])
    m, unused = auto(psd)
    got = [(e["role"], e["sources"]) for e in m["layers"]]
    assert ("back_hair", ["横髪まとめ/左横髪裏"]) in got
    assert ("mouth_closed", ["口閉じ/口閉じ線上", "口閉じ/リップハイライト"]) in got
    assert ("eye_l_highlight", ["右目/右瞳/右瞳hi1"]) in got
    assert ("front_hair", ["帽子"]) in got
    assert ("body", ["しっぽと後系/しっぽ"]) in got
    assert unused == ["用紙(背景と判断)", "背景(インポート時削除)(背景と判断)"]


def test_unknown_bottom_layer_covering_canvas_is_background():
    L = FakeLayer
    psd = FakePsd([L("レイヤー 0", bbox=(0, 0, 3000, 7800)), L("顔", bbox=(1100, 800, 1900, 1800)),
                   L("透かし", bbox=(0, 0, 3000, 7800))])
    m, unused = auto(psd)
    assert unused == ["レイヤー 0(一番奥でキャンバス全体を覆うので、背景と判断)"]
    assert [e["sources"] for e in m["layers"]] == [["顔"], ["透かし"]]  # 手前の透かしは消さない


def test_expressions_skip_rough_sketches_and_mouth_line_parts():
    L = FakeLayer
    psd = FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("口閉じ", [L("口閉じ線上", bbox=(1400, 1550, 1600, 1580)),
                   L("口閉じ線下", visible=False, bbox=(1400, 1550, 1600, 1580))]),
        L("表情見本", [L("笑顔ラフ", bbox=(1100, 800, 1900, 1800))], visible=False),
        L("目", [L("笑顔目ラフ目安", visible=False, bbox=(1200, 1100, 1400, 1200))]),
    ])
    m, _ = auto(psd)
    assert not m.get("expressions")
    assert not m.get("mouth_frames")


def test_choices_cover_roles_in_body_order():
    import psd_editor as e

    labels = [c[0] for c in e.CHOICES]
    assert len(labels) == len(set(labels))
    assert {c[1] for c in e.CHOICES} - {None} == set(t.ROLES)
    assert not any("画面" in x for x in labels + list(t.ROLE_LABELS.values()) + list(t.EXPR_SLOT_LABELS.values()))
    order = [c[1] for c in e.CHOICES]
    # 髪 → 顔 → 眉 → 目(左の一式 → 右の一式) → 口 → 体 → 腕 → 脚
    first = [order.index(r) for r in ("back_hair", "face", "brow_l", "eye_l_white", "eye_r_white",
                                       "mouth_closed", "body", "arm_l_upper", "arm_r_upper", "leg_l_upper", "leg_r_upper")]
    assert first == sorted(first)
    assert max(i for i, r in enumerate(order) if r and r.startswith("eye_l_")) < order.index("eye_r_white")


def test_swap_sides():
    import psd_editor as e

    L = FakeLayer
    psd = FakePsd([L("顔"), L("eye_l_white"), L("eye_r_pupil"), L("hand_l"), L("体"), L("涙")])
    items = t.leaves(psd)
    picks = [t.Pick(items[0], "face"), t.Pick(items[1], "eye_l_white", part="eye_l_white"),
             t.Pick(items[2], "eye_r_pupil"), t.Pick(items[3], "hand_l"), t.Pick(items[4], "body"),
             t.Pick(items[5], None)]
    exprs = [t.Expr("泣き", {"eye_l": [5], "extra": [4]}), t.Expr("ウインク", {"eye_r": [2]})]
    assert e.swap_sides(picks, exprs) == 5
    assert [p.role for p in picks] == ["face", "eye_r_white", "eye_l_pupil", "hand_r", "body", None]
    assert picks[1].part is None and picks[1].how == t.HOW_MANUAL and picks[0].how == ""
    assert exprs[0].slots == {"eye_r": [5], "extra": [4]} and exprs[1].slots == {"eye_l": [2]}
    e.swap_sides(picks, exprs)  # もう一度で元に戻る
    assert [p.role for p in picks] == ["face", "eye_l_white", "eye_r_pupil", "hand_l", "body", None]
    assert e.swap_sides([picks[0]], []) == 0


def test_guess_nose_names():
    # 鼻は独立した役割(影・ハイライトも鼻にまとめる)。「はなび」「ハナビ」などは鼻にしない
    for n in ("鼻", "鼻の影", "鼻ハイライト", "はな", "はなの影", "ハナ", "nose", "Nose_Shadow", "NoseHL", "nostril", "鼻子"):
        assert t.guess((n,)) == "nose", n
    for n in ("はなび", "はなし", "ハナビ", "花"):
        assert t.guess((n,)) != "nose", n
    assert t.guess(("影", "鼻")) == "nose"  # 「鼻」グループの中の影
    assert t.guess(("頬ハイライト",)) == "face" and t.guess(("耳",)) == "face"
    assert t.parse_part_name("nose") == ("nose", "nose", None)
    assert t.parse_part_name("Nose@0.4") == ("nose", "Nose", 0.4)
    assert "nose" in t.FUNCTIONAL_ROLES and t.ROLE_DEFAULTS["nose"] == ("head", 0.25)


def test_auto_nose_part_merges_shading():
    L = FakeLayer
    psd = FakePsd([L("顔", bbox=(1100, 800, 1900, 1800)),
                   L("鼻", [L("鼻線", bbox=(1480, 1300, 1520, 1380)), L("影", bbox=(1470, 1300, 1530, 1390))]),
                   L("鼻ハイライト", bbox=(1490, 1310, 1500, 1330)), L("頬", bbox=(1200, 1350, 1800, 1450))])
    m, _ = auto(psd)
    nose = [e for e in m["layers"] if e["role"] == "nose"]
    assert len(nose) == 1 and nose[0]["group"] == "head" and nose[0]["depth"] == 0.25
    assert nose[0]["sources"] == ["鼻/鼻線", "鼻/影", "鼻ハイライト"]
    assert not t.validate_mapping(m)


def test_auto_parts_split_per_layer_but_keep_blend_layers():
    """自動の部品はレイヤーごと。通常以外の描画モード(乗算の影など)は下のレイヤーと同じ部品にする(合成した絵が要るため)。"""
    L = FakeLayer
    shade = L("影", bbox=(1100, 3000, 1900, 4000))
    shade.blend_mode = "MULTIPLY"
    psd = FakePsd([
        L("顔", bbox=(1100, 800, 1900, 1800)),
        L("体", [L("服の裏", bbox=(1000, 2000, 2000, 4000)), L("服", bbox=(1000, 2000, 2000, 4000)), shade,
                L("すそ", bbox=(1000, 3800, 2000, 4200))]),
    ])
    m, _ = auto(psd)
    got = [(e["name"], e["sources"]) for e in m["layers"] if e["role"] == "body"]
    assert got == [("body", ["体/服の裏"]), ("body_2", ["体/服", "体/影"]), ("body_3", ["体/すそ"])]
    assert not t.validate_mapping(m)
