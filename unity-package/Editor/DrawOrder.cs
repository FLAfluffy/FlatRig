using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>
    /// パーツの表示順(奥 → 手前)の指定(docs/SPEC.md §5.12)。生成ウィンドウで並べ替え、RigBuilder がこの順で描く。
    /// キーはレイヤー名。腕・手の手前用の複製(動かしている間だけ表示。§5.10)は レイヤー名 + "_Front"。
    /// </summary>
    public static class DrawOrder
    {
        public static string FrontKey(string layerName) => layerName + RigBuilder.FrontSuffix;

        /// <summary>手前用の複製を作るレイヤーの添字(上腕 → 前腕 → 手、同じ部位はレイヤーの順)。hasJoint は関節ノードがあるか。</summary>
        public static int[] FrontIndices(IList<LayerDef> ordered, Func<LayerDef, bool> hasJoint) =>
            Enumerable.Range(0, ordered.Count)
                .Where(i => hasJoint(ordered[i]) && LimbRig.IsArmRole(ordered[i].role))
                .OrderBy(i => ordered[i].role.StartsWith("hand") ? 2 : ordered[i].role.EndsWith("_lower") ? 1 : 0).ThenBy(i => i)
                .ToArray();

        /// <summary>既定の順: レイヤーの order 順(PSD の重なり順)、その後に手前用の複製。</summary>
        public static List<string> DefaultKeys(IList<LayerDef> ordered, IEnumerable<int> fronts) =>
            ordered.Select(l => l.name).Concat(fronts.Select(i => FrontKey(ordered[i].name))).ToList();

        /// <summary>
        /// 保存した順(saved)を既定の順(defaults)に合わせる。saved に無いキーは、既定の順で 1 つ奥にあるキーのすぐ手前に入れる。
        /// 今は無いキー(消したレイヤーなど)は捨てる。saved が空なら既定の順。
        /// </summary>
        public static List<string> Merge(IList<string> defaults, IEnumerable<string> saved)
        {
            var known = new HashSet<string>(defaults);
            var result = (saved ?? Enumerable.Empty<string>()).Where(known.Contains).Distinct().ToList();
            var placed = new HashSet<string>(result);
            for (var i = 0; i < defaults.Count; i++)
            {
                if (!placed.Add(defaults[i])) continue;
                result.Insert(i == 0 ? 0 : result.IndexOf(defaults[i - 1]) + 1, defaults[i]);
            }
            return result;
        }

        /// <summary>PSD ツールの mapping.json(layers フォルダの 1 つ上)から、レイヤー名 → 元の PSD レイヤー名。無ければ空。</summary>
        public static Dictionary<string, string[]> PsdSources(string layersJsonPath)
        {
            var map = new Dictionary<string, string[]>();
            try
            {
                var dir = Path.GetDirectoryName(Path.GetFullPath(layersJsonPath));
                var path = dir == null ? null : Path.Combine(Path.GetDirectoryName(dir) ?? dir, "mapping.json");
                if (path == null || !File.Exists(path)) return map;
                var doc = JsonUtility.FromJson<MappingDoc>(File.ReadAllText(path));
                foreach (var e in doc?.layers ?? new MappingEntry[0])
                    if (!string.IsNullOrEmpty(e.name) && e.sources != null)
                        map[e.name] = e.sources.Select(src => src.Split('/').Last().Replace("\\", "")).ToArray();
            }
            catch (Exception)
            {
                // 読めなければ元の名前は出さない(表示だけの情報)
            }
            return map;
        }

        [Serializable]
        class MappingDoc
        {
            public MappingEntry[] layers;
        }

        [Serializable]
        class MappingEntry
        {
            public string name;
            public string[] sources;
        }
    }
}
