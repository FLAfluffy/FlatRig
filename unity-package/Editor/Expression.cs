using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>
    /// 表情と、絵の差し替え(docs/SPEC.md §5.9)。表示の切り替えはすべてシェーダのアルファで行い、ボーンは使わない
    /// (既存の動き(口の開閉・まばたきなど)のツリーと書き込み先がぶつからない)。頂点の UV4 に次を入れる(Uv4):
    ///   x: 表示する表情のビット(bit 0 = 通常、bit k = k 番目の表情)。マテリアルの _FR_ExprA〜D(表情ごとの重み)との積がアルファに掛かる
    ///   y, z: 口の絵の差し替え。y = この絵になる開き具合(基準)、z = 隣の絵の基準までの幅。開き具合(_FR_MouthOpen)が
    ///         基準から離れるほど薄くなり、幅だけ離れると消える(隣の絵と連続して混ざる)。口の絵でない部品は z ≥ 50(常に表示)
    ///   w: 目の絵の差し替え。1/3 = 左/右の通常の目の部品(閉じたら隠す)、2/4 = 左/右の閉じた目の絵(閉じたら出す)
    ///      閉じたかどうかは _FR_EyeOpenL/R(開き具合)が _FR_EyeClosedAt より小さいか
    /// 表情の部品(role "expr_&lt;枠&gt;"、layers.json の expr = 表情の番号)はその表情のときだけ表示し、
    /// その表情が部品を持つ枠(左目・右目・口)の通常の部品は、その表情の間だけ隠す。口はどれか 1 つの枠があれば口全体を入れ替える。
    /// </summary>
    public static class Expression
    {
        public const int MaxExpressions = 15;  // 通常を含めて 16(_FR_ExprA〜D の 4 成分 × 4)
        public static readonly string[] WeightProps = { "_FR_ExprA", "_FR_ExprB", "_FR_ExprC", "_FR_ExprD" };
        public const string MouthOpenProp = "_FR_MouthOpen";
        public const string EyeOpenPropL = "_FR_EyeOpenL";
        public const string EyeOpenPropR = "_FR_EyeOpenR";
        public const string EyeClosedAtProp = "_FR_EyeClosedAt";
        public const float DefaultEyeClosedAt = 0.12f;  // 開き具合がこれより小さいと閉じた目の絵にする(まつ毛が下りきる直前)

        public static string EyeOpenProp(string side) => side == "l" ? EyeOpenPropL : EyeOpenPropR;

        public static readonly string[] Slots = { "eye_l", "eye_r", "mouth_closed", "mouth_half", "mouth_open", "extra" };
        public static readonly string[] MouthFrames = { "mouth_closed", "mouth_half", "mouth_open" };
        public const string Prefix = "expr_";

        /// <summary>前の版の笑顔の役割名 → 枠。layers.json に expressions が無ければ、表情「笑顔」(番号 1)として扱う。</summary>
        public const string LegacySmileName = "笑顔";
        static readonly Dictionary<string, string> LegacySmile = new Dictionary<string, string>
        {
            { "eye_l_smile", "eye_l" }, { "eye_r_smile", "eye_r" }, { "mouth_smile", "mouth_closed" },
            { "mouth_smile_open", "mouth_open" }, { "smile_extra", "extra" },
        };
        public static IEnumerable<string> LegacyRoles => LegacySmile.Keys;
        public static IEnumerable<string> ExprRoles => Slots.Select(s => Prefix + s);

        /// <summary>表情の名前(番号 1 から)。</summary>
        public static string[] Names(LayersDoc doc)
        {
            if (doc.expressions != null && doc.expressions.Length > 0) return doc.expressions;
            return doc.layers.Any(l => LegacySmile.ContainsKey(l.role)) ? new[] { LegacySmileName } : Array.Empty<string>();
        }

        public static bool HasExpressions(LayersDoc doc) => Names(doc).Length > 0;

        /// <summary>表情の部品なら枠(eye_l など)、それ以外は null。</summary>
        public static string Slot(LayerDef l)
        {
            if (l.role != null && l.role.StartsWith(Prefix)) return l.role.Substring(Prefix.Length);
            return l.role != null && LegacySmile.TryGetValue(l.role, out var slot) ? slot : null;
        }

        /// <summary>表情の部品なら表情の番号(1〜)、それ以外は 0。</summary>
        public static int Index(LayerDef l) => Slot(l) == null ? 0 : l.expr > 0 ? l.expr : 1;

        public static bool IsMouthSlot(string slot) => slot != null && slot.StartsWith("mouth_");

        /// <summary>口の部品(通常・表情とも)。口の形(横幅)で動かす。</summary>
        public static bool IsMouth(LayerDef l) => l.role.StartsWith("mouth_") || IsMouthSlot(Slot(l));

        /// <summary>通常の部品が属する入れ替えの単位(eye_l / eye_r / mouth)。入れ替えない部品は null。</summary>
        static string NormalGroup(string role)
        {
            if (role.StartsWith("eye_l_")) return "eye_l";
            if (role.StartsWith("eye_r_")) return "eye_r";
            return Array.IndexOf(MouthFrames, role) >= 0 ? "mouth" : null;
        }

        static string SlotGroup(string slot) => IsMouthSlot(slot) ? "mouth" : slot;

        /// <summary>レイヤー 1 枚ぶんの UV4。</summary>
        public static Vector4 Uv4(LayersDoc doc, LayerDef layer)
        {
            var count = Math.Min(Names(doc).Length, MaxExpressions);
            var slot = Slot(layer);
            float mask;
            Vector2 range;
            if (slot != null)
            {
                var k = Index(layer);
                mask = k <= count ? 1 << k : 0;
                range = IsMouthSlot(slot)
                    ? FrameRange(doc.layers.Where(l => Index(l) == k).Select(Slot), slot)
                    : Always;
            }
            else
            {
                var group = NormalGroup(layer.role);
                var bits = 1;  // 通常
                for (var k = 1; k <= count; k++)
                {
                    var kk = k;
                    var overridden = group != null && doc.layers.Any(l => Index(l) == kk && SlotGroup(Slot(l)) == group);
                    if (!overridden) bits |= 1 << k;
                }
                mask = bits;
                range = Array.IndexOf(MouthFrames, layer.role) < 0 ? Always
                    : doc.mouth_frames ? FrameRange(doc.layers.Select(l => Slot(l) == null ? l.role : null), layer.role)
                    : layer.role == "mouth_half" ? Never  // 絵の差し替えでない口では半開きの絵は使わない
                    : Always;
            }
            return new Vector4(mask, range.x, range.y, EyeCode(doc, layer));
        }

        static readonly Vector2 Always = new Vector2(0f, 99f);
        static readonly Vector2 Never = new Vector2(99f, 0.001f);

        /// <summary>
        /// 口の絵の差し替えで、frame(mouth_closed / mouth_half / mouth_open)の (基準の開き具合, 隣の基準までの幅)。
        /// present はその口の組にある枠。3 枚なら 0 / 0.5 / 1、2 枚なら 0 / 1 が基準で、その間は隣の絵と混ざる。1 枚なら常に表示。
        /// </summary>
        public static Vector2 FrameRange(IEnumerable<string> present, string frame)
        {
            var set = new HashSet<string>(present.Where(s => s != null));
            var frames = MouthFrames.Where(set.Contains).ToArray();
            var i = Array.IndexOf(frames, frame);
            if (i < 0) return Never;
            if (frames.Length == 1) return Always;
            var keys = Keys(frames.Length);
            return new Vector2(keys[i], keys[1] - keys[0]);
        }

        /// <summary>口の絵が n 枚のときの、それぞれの絵になる開き具合(閉じ 0 〜 開き 1 を等分)。</summary>
        public static float[] Keys(int n) => Enumerable.Range(0, n).Select(i => n == 1 ? 0f : i / (float)(n - 1)).ToArray();

        /// <summary>
        /// 開き具合で切り替える口の絵の組(閉じ → 開きの順)。通常の口(mouth_frames のとき)と、表情ごとの口。2 枚以上の組だけ。
        /// </summary>
        public static List<LayerDef[]> MouthSets(LayersDoc doc)
        {
            var sets = new List<LayerDef[]>();
            LayerDef[] Sorted(IEnumerable<LayerDef> layers, Func<LayerDef, string> frame) =>
                layers.Where(l => Array.IndexOf(MouthFrames, frame(l)) >= 0)
                    .GroupBy(frame).Select(g => g.First())
                    .OrderBy(l => Array.IndexOf(MouthFrames, frame(l))).ToArray();
            if (doc.mouth_frames) sets.Add(Sorted(doc.layers.Where(l => Slot(l) == null), l => l.role));
            foreach (var g in doc.layers.Where(l => IsMouthSlot(Slot(l))).GroupBy(Index))
                sets.Add(Sorted(g, Slot));
            return sets.Where(x => x.Length >= 2).ToList();
        }

        /// <summary>UV4.w。その側に閉じた目の絵があるときだけ、通常の目の部品と閉じた目の絵を切り替える。</summary>
        static float EyeCode(LayersDoc doc, LayerDef layer)
        {
            foreach (var (side, code) in new[] { ("l", 1f), ("r", 3f) })
            {
                var closed = $"eye_{side}_closed";
                if (layer.role == closed) return code + 1f;
                if (layer.role.StartsWith($"eye_{side}_") && doc.layers.Any(l => l.role == closed)) return code;
            }
            return 0f;
        }

        /// <summary>表情 k(0 = 通常)の重み。_FR_ExprA〜D の順に 4 成分ずつ。</summary>
        public static Vector4 Weights(int k, int prop) => new Vector4(
            k == prop * 4 ? 1f : 0f, k == prop * 4 + 1 ? 1f : 0f, k == prop * 4 + 2 ? 1f : 0f, k == prop * 4 + 3 ? 1f : 0f);

        /// <summary>表情の数 count のときに使う _FR_ExprA〜D の数。</summary>
        public static int WeightPropCount(int count) => Math.Min(WeightProps.Length, count / 4 + 1);
    }
}
