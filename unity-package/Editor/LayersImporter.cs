using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>layers.json の 1 レイヤー(docs/SPEC.md §5.3)。</summary>
    [Serializable]
    public class LayerDef
    {
        public string name;
        public string role;
        public string group;
        public string file;
        public float[] bbox;   // [x0, y0, x1, y1] キャンバス座標(px, 左上原点)。x1, y1 は含まない
        public float[] pivot;  // [px, py]
        public float depth;    // −1(奥)〜+1(手前)
        public int order;      // 小さいほど奥。レイヤーごとに一意
        public bool hide_when_open;  // mouth_closed のみ。口の隠しの構造: 口を開くと閉じた口を消す
        public float open_from_width;  // 開いた口のみ。隠しの構造での開き始めの横幅の比(0 = 使わない)
        public float[] joint;  // 任意。手足の部品の根元の関節 [px, py](無ければ絵から求める。LimbRig)
        public int expr;       // 表情の部品(role "expr_<枠>")のみ。表情の番号(1〜。expressions の並び。Expression)

        public float Width => bbox[2] - bbox[0];
        public float Height => bbox[3] - bbox[1];
    }

    /// <summary>layers.json 全体。</summary>
    [Serializable]
    public class LayersDoc
    {
        public int[] canvas;        // [幅, 高さ]
        public float[] head_pivot;  // 任意。省略時は face の bbox 下端中央
        public float[] body_pivot;  // 任意。省略時はキャンバス下端中央
        public string[] expressions;  // 任意。表情の名前(番号 1 から。Expression)
        public bool mouth_frames;     // 任意。口は閉じ・半開き・開きの絵の差し替え(Expression)
        public LayerDef[] layers;
    }

    /// <summary>読み込み済みのレイヤー群。Textures は Ordered と同じ並び。</summary>
    public class LoadedLayers
    {
        public LayersDoc Doc;
        public string JsonText;
        public LayerDef[] Ordered;
        public Texture2D[] Textures;

        public void DestroyTextures()
        {
            if (Textures == null) return;
            foreach (var tex in Textures)
            {
                if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
            }
            Textures = null;
        }
    }

    /// <summary>layers.json と PNG 群の読み込み・検証、アトラスの作成。</summary>
    public static class LayersImporter
    {
        /// <summary>role の固定語彙(SPEC §5.4)。増やすときは SPEC に追記してから。</summary>
        public static readonly string[] Roles =
        {
            "back_hair", "body", "face", "eye_l_white", "eye_l_pupil", "eye_r_white",
            "eye_r_pupil", "brow_l", "brow_r", "mouth_closed", "mouth_open", "front_hair",
            "eye_l_lash", "eye_r_lash", "eye_l_shadow", "eye_r_shadow", "eye_l_highlight", "eye_r_highlight",
            "eye_l_closed", "eye_r_closed", "mouth_half",  // 閉じた目・半開きの口の絵(差し替え。Expression)
            "expr_eye_l", "expr_eye_r", "expr_mouth_closed", "expr_mouth_half", "expr_mouth_open", "expr_extra",  // 表情の部品
            "eye_l_smile", "eye_r_smile", "mouth_smile", "mouth_smile_open", "smile_extra",  // 前の版の笑顔(表情「笑顔」として扱う)
            "arm_l_upper", "arm_l_lower", "hand_l", "arm_r_upper", "arm_r_lower", "hand_r",  // 手足(LimbRig。_l は画面の左)
            "leg_l_upper", "leg_l_lower", "foot_l", "leg_r_upper", "leg_r_lower", "foot_r",
            "nose",  // 鼻(影・ハイライトも含めて 1 つ。頭の視差だけで動く)
        };

        public static readonly string[] Groups = { "head", "body" };

        public static LoadedLayers Load(string jsonPath, List<string> errors)
        {
            var doc = LoadDoc(jsonPath, errors, out var text);
            if (doc == null) return null;

            var dir = Path.GetDirectoryName(Path.GetFullPath(jsonPath));
            var loaded = new LoadedLayers
            {
                Doc = doc,
                JsonText = text,
                Ordered = doc.layers.OrderBy(l => l.order).ToArray(),
            };
            loaded.Textures = new Texture2D[loaded.Ordered.Length];

            for (var i = 0; i < loaded.Ordered.Length; i++)
            {
                var layer = loaded.Ordered[i];
                var path = Path.Combine(dir, layer.file);
                if (!File.Exists(path))
                {
                    errors.Add($"{layer.name}: 画像が見つかりません: {path}");
                    continue;
                }

                var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false) { name = layer.name };
                loaded.Textures[i] = tex;
                if (!ImageConversion.LoadImage(tex, File.ReadAllBytes(path)))
                {
                    errors.Add($"{layer.name}: PNG として読めません: {path}");
                    continue;
                }

                var w = Mathf.RoundToInt(layer.Width);
                var h = Mathf.RoundToInt(layer.Height);
                if (tex.width != w || tex.height != h)
                {
                    errors.Add($"{layer.name}: 画像の大きさ {tex.width}x{tex.height} が bbox の {w}x{h} と一致しません");
                }
            }

            if (errors.Count > 0)
            {
                loaded.DestroyTextures();
                return null;
            }
            return loaded;
        }

        /// <summary>layers.json を読んで検証する(画像は読まない)。</summary>
        public static LayersDoc LoadDoc(string jsonPath, List<string> errors, out string text)
        {
            text = null;
            if (string.IsNullOrEmpty(jsonPath) || !File.Exists(jsonPath))
            {
                errors.Add($"layers.json が見つかりません: {jsonPath}");
                return null;
            }

            LayersDoc doc;
            try
            {
                text = File.ReadAllText(jsonPath);
                doc = JsonUtility.FromJson<LayersDoc>(text);
            }
            catch (Exception e)
            {
                errors.Add($"layers.json を読めません: {e.Message}");
                return null;
            }

            if (doc == null)
            {
                errors.Add("layers.json が空です");
                return null;
            }

            Validate(doc, errors);
            return errors.Count > 0 ? null : doc;
        }

        public static void Validate(LayersDoc doc, List<string> errors)
        {
            if (doc.canvas == null || doc.canvas.Length != 2 || doc.canvas[0] <= 0 || doc.canvas[1] <= 0)
            {
                errors.Add("canvas は [幅, 高さ](正の整数)で指定してください");
                return;
            }
            if (doc.layers == null || doc.layers.Length == 0)
            {
                errors.Add("layers が空です");
                return;
            }
            if (HasValue(doc.head_pivot) && doc.head_pivot.Length != 2) errors.Add("head_pivot は [px, py] で指定してください");
            if (HasValue(doc.body_pivot) && doc.body_pivot.Length != 2) errors.Add("body_pivot は [px, py] で指定してください");

            var exprCount = doc.expressions?.Length ?? 0;
            if (exprCount > Expression.MaxExpressions) errors.Add($"表情は {Expression.MaxExpressions} 個までです");
            if (doc.expressions != null)
            {
                if (doc.expressions.Any(string.IsNullOrWhiteSpace)) errors.Add("expressions に空の名前があります");
                if (doc.expressions.Distinct().Count() != exprCount) errors.Add("expressions の名前が重複しています");
            }
            if (exprCount > 0 && doc.layers.Any(l => Expression.LegacyRoles.Contains(l.role)))
                errors.Add("前の版の笑顔の役割名(eye_l_smile など)と expressions は一緒に使えません。FlatRigPSD で書き出し直してください");

            float cw = doc.canvas[0], ch = doc.canvas[1];
            var names = new HashSet<string>();
            var orders = new HashSet<int>();
            foreach (var l in doc.layers)
            {
                var label = string.IsNullOrEmpty(l.name) ? "(名前なし)" : l.name;
                if (string.IsNullOrEmpty(l.name)) errors.Add("name が空のレイヤーがあります");
                else if (!names.Add(l.name)) errors.Add($"name が重複しています: {l.name}");
                if (!orders.Add(l.order)) errors.Add($"{label}: order {l.order} が重複しています(レイヤーごとに一意)");
                if (Array.IndexOf(Roles, l.role) < 0) errors.Add($"{label}: role \"{l.role}\" は語彙にありません(SPEC §5.4)");
                if (Array.IndexOf(Groups, l.group) < 0) errors.Add($"{label}: group は \"head\" か \"body\" です");
                if (!IsSafePng(l.file)) errors.Add($"{label}: file は layers.json と同じフォルダ以下の .png を指定してください");
                if (l.depth < -1f || l.depth > 1f) errors.Add($"{label}: depth は −1〜1 です");
                if (l.pivot == null || l.pivot.Length != 2) errors.Add($"{label}: pivot は [px, py] で指定してください");
                if (HasValue(l.joint) && l.joint.Length != 2) errors.Add($"{label}: joint は [px, py] で指定してください");
                if (l.role != null && l.role.StartsWith(Expression.Prefix) && (l.expr < 1 || l.expr > exprCount))
                    errors.Add($"{label}: expr は 1〜{exprCount}(expressions の番号)です");

                if (l.bbox == null || l.bbox.Length != 4)
                {
                    errors.Add($"{label}: bbox は [x0, y0, x1, y1] で指定してください");
                }
                else if (l.bbox[2] <= l.bbox[0] || l.bbox[3] <= l.bbox[1])
                {
                    errors.Add($"{label}: bbox は x0 < x1, y0 < y1 にしてください");
                }
                else if (l.bbox[0] < 0 || l.bbox[1] < 0 || l.bbox[2] > cw || l.bbox[3] > ch)
                {
                    errors.Add($"{label}: bbox がキャンバスからはみ出しています");
                }
            }
        }

        /// <summary>
        /// レイヤー画像を 1 枚のアトラスにまとめる。rects は textures と同じ並び(UV, 0〜1)。
        /// 最大サイズに収まらず縮小された場合は warnings に追記する。
        /// </summary>
        public static Texture2D BuildAtlas(Texture2D[] textures, int padding, int maxSize, out Rect[] rects,
            List<string> errors, List<string> warnings)
        {
            var atlas = new Texture2D(2, 2, TextureFormat.RGBA32, false) { name = "atlas" };
            rects = atlas.PackTextures(textures, padding, maxSize, false);
            if (rects == null || rects.Length != textures.Length)
            {
                errors.Add("アトラスの作成に失敗しました");
                UnityEngine.Object.DestroyImmediate(atlas);
                return null;
            }

            for (var i = 0; i < textures.Length; i++)
            {
                if (rects[i].width * atlas.width < textures[i].width - 0.5f)
                {
                    warnings.Add($"アトラスが {maxSize}px に収まらず縮小されました(画質が下がります)。「アトラスの最大サイズ」を上げるか、素材を小さくしてください");
                    break;
                }
            }
            return atlas;
        }

        static bool HasValue(float[] values) => values != null && values.Length > 0;

        static bool IsSafePng(string file)
        {
            if (string.IsNullOrEmpty(file) || Path.IsPathRooted(file)) return false;
            if (file.Replace('\\', '/').Split('/').Contains("..")) return false;
            return string.Equals(Path.GetExtension(file), ".png", StringComparison.OrdinalIgnoreCase);
        }
    }
}
