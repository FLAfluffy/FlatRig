using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>まぶたの動きに使う、片目ぶんの寸法(キャンバス px)。生成時に素材の不透明部分から測り、rig.json に保存する。</summary>
    [Serializable]
    public class EyeLidInfo
    {
        public bool valid;
        public bool has_lash;
        public float clip_offset_px;    // 白目の上端からまつ毛の下端まで(まつ毛に隠れている幅。中央付近の最小値)
        public float travel_px;         // 閉じきるまでにまつ毛が下がる量
        public float lash_anchor_y_px;  // まつ毛を平たくするときの基準(中央付近のまつ毛の下端、キャンバス y)
    }

    /// <summary>
    /// まばたきの「まぶた」表現(docs/SPEC.md §5.4)。
    /// まつ毛を下げながら上下に平たくし、白目と黒目は縦に潰さず、まつ毛の下端より上をシェーダで切り取る。
    /// 切り取りの線は「白目の上端の形を下にずらしたもの」で、ずらす量(px)をマテリアルの _FR_LidL / _FR_LidR で渡す。
    /// 白目の影(eye_*_shadow)はまつ毛と同じだけ下がり(_FR_LashL / _FR_LashR = 下がり量 px)、白目の外と切り取り線より上は消す。
    /// </summary>
    public static class EyeLid
    {
        public const string LidPropL = "_FR_LidL";
        public const string LidPropR = "_FR_LidR";
        public const string LashPropL = "_FR_LashL";
        public const string LashPropR = "_FR_LashR";
        public const string HighlightPropL = "_FR_HiL";
        public const string HighlightPropR = "_FR_HiR";

        /// <summary>瞳のハイライトがまぶたに押されて下がれる量の既定値(黒目の下端までの余白に対する割合)。</summary>
        public const float DefaultHighlightFollow = 0.5f;

        /// <summary>閉じた目の、まつ毛の縦の縮尺の既定値(AnimatorSettings.EyeLashClosedScale)。</summary>
        public const float DefaultLashClosedScale = 0.6f;

        /// <summary>この開き具合より上で、切り取り線をまつ毛の下端に追いつかせる(開ききった状態では何も切らない)。</summary>
        public const float CatchUpAt = 0.9f;

        public static string LidProp(string side) => side == "l" ? LidPropL : LidPropR;
        public static string LashProp(string side) => side == "l" ? LashPropL : LashPropR;
        public static string HighlightProp(string side) => side == "l" ? HighlightPropL : HighlightPropR;

        /// <summary>
        /// 開き具合 v での瞳のハイライトの下がり量(px)。まぶたの線(まつ毛の下端)がハイライトの上端に届いたら押し下げ、
        /// maxPush まで下がったら止まる(その後はまぶたに隠れる)。lineAtRest はまつ毛が無いときに使う静止時の線(白目の上端)。
        /// </summary>
        public static float HighlightPush(EyeLidInfo e, float highlightTop, float maxPush, float lineAtRest, float v)
        {
            var line = e.has_lash ? e.lash_anchor_y_px + LashDrop(e, v) : lineAtRest + ClipShift(e, v);
            return Mathf.Clamp(line - highlightTop, 0f, Mathf.Max(0f, maxPush));
        }

        /// <summary>白目とまつ毛(無ければ null)の素材から寸法を測る。白目が空なら valid=false。</summary>
        public static EyeLidInfo Measure(LayerDef white, Texture2D whiteTex, LayerDef lash, Texture2D lashTex)
        {
            var info = new EyeLidInfo();
            var w = Columns(white, whiteTex);
            if (w.Count == 0) return info;

            var height = w.Values.Max(c => c.bottom - c.top);
            var l = lash != null && lashTex != null ? Columns(lash, lashTex) : new Dictionary<int, (float top, float bottom)>();
            var offset = 0f;
            var anchor = 0f;
            if (l.Count > 0)
            {
                // 端はまつ毛の跳ねや白目の角で形が合わないので、重なる範囲の中央 60% だけで測る
                var x0 = Math.Max(w.Keys.Min(), l.Keys.Min());
                var x1 = Math.Min(w.Keys.Max(), l.Keys.Max());
                var margin = (x1 - x0) / 5;
                var gaps = Enumerable.Range(x0 + margin, Math.Max(0, x1 - x0 - 2 * margin + 1))
                    .Where(x => w.ContainsKey(x) && l.ContainsKey(x))
                    .Select(x => l[x].bottom - w[x].top).ToList();
                offset = gaps.Count > 0 ? Mathf.Max(0f, gaps.Min()) : 0f;

                var lx0 = l.Keys.Min();
                var lx1 = l.Keys.Max();
                var center = l.Where(kv => kv.Key >= lx0 + (lx1 - lx0) * 2 / 5 && kv.Key <= lx0 + (lx1 - lx0) * 3 / 5)
                    .Select(kv => kv.Value.bottom).OrderBy(v => v).ToList();
                if (center.Count == 0) center = l.Values.Select(c => c.bottom).OrderBy(v => v).ToList();
                anchor = center[center.Count / 2];
            }

            info.valid = true;
            info.has_lash = l.Count > 0;
            info.clip_offset_px = offset;
            info.travel_px = Mathf.Max(1f, height - offset + 1f);
            info.lash_anchor_y_px = anchor;
            return info;
        }

        /// <summary>開き具合 v(0=閉じ, 1=開き)でのまつ毛の下がり量(px)。</summary>
        public static float LashDrop(EyeLidInfo e, float v) => (1f - v) * e.travel_px;

        /// <summary>開き具合 v での切り取りのずらし量(px)。v=1 では 0(何も切らない)。</summary>
        public static float ClipShift(EyeLidInfo e, float v)
        {
            var catchUp = Mathf.Clamp01((1f - v) / (1f - CatchUpAt));
            return LashDrop(e, v) + e.clip_offset_px * catchUp;
        }

        /// <summary>開き具合 v でのまつ毛の縦の縮尺。</summary>
        public static float LashScale(float closedScale, float v) => Mathf.Lerp(closedScale, 1f, v);

        /// <summary>列(キャンバス x)ごとの不透明部分の上端・下端(キャンバス y、下端は含まない)。</summary>
        static Dictionary<int, (float top, float bottom)> Columns(LayerDef layer, Texture2D tex)
        {
            var result = new Dictionary<int, (float top, float bottom)>();
            if (tex == null || tex.width <= 0 || tex.height <= 0) return result;
            Color32[] px;
            try
            {
                px = tex.GetPixels32();
            }
            catch (UnityException)
            {
                return result;  // 読み取り不可のテクスチャ
            }
            var sx = layer.Width / tex.width;
            var sy = layer.Height / tex.height;
            for (var x = 0; x < tex.width; x++)
            {
                int top = -1, bottom = -1;  // 画像の上から数えた行
                for (var r = 0; r < tex.height; r++)
                {
                    // GetPixels32 は下の行から並ぶ
                    if (px[(tex.height - 1 - r) * tex.width + x].a < 128) continue;
                    if (top < 0) top = r;
                    bottom = r;
                }
                if (top < 0) continue;
                var cx = Mathf.FloorToInt(layer.bbox[0] + (x + 0.5f) * sx);
                result[cx] = (layer.bbox[1] + top * sy, layer.bbox[1] + (bottom + 1) * sy);
            }
            return result;
        }
    }
}
