using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>params.json の 1 パラメータ(docs/SPEC.md §3.1)。Unity 側は avatar・mode・bits・phase だけ使う。</summary>
    [Serializable]
    public class ParamEntry
    {
        public string avatar;
        public string mode;   // "signed"(−1〜1)/ "unsigned"(0〜1)
        public bool synced;
        public int bits;
        public int phase;

        public bool Signed => mode == "signed";
        public float Min => Signed ? -1f : 0f;
        /// <summary>アバター内部の平滑化済みローカル float(同期しない)。</summary>
        public string Smoothed => avatar + "_s";
    }

    /// <summary>params.json の flags の 1 項目。type は "bool" か "int"(表情の番号 FR_Expr など)。</summary>
    [Serializable]
    public class FlagEntry
    {
        public string avatar;
        public string type;
        public bool synced;
        public int bits;

        public bool IsInt => type == "int";
    }

    [Serializable]
    public class ParamsDoc
    {
        public string avatar_param_prefix;
        public ParamEntry[] @params;
        public FlagEntry[] flags;
    }

    /// <summary>params.json(ブリッジと共通の「唯一の正」)の読み込みと最小限の検証。</summary>
    public static class ParamsFile
    {
        public const string ActiveFlag = "FR_Active";
        public const string EnableFlag = "FR_Enable";
        public const string ExprFlag = "FR_Expr";  // 表情の番号(Expression)。無い params.json でも動く(表情は付かない)
        public const string AutoBlinkFlag = "FR_AutoBlink";  // 自動まばたき(メニューのトグル)。無い params.json でも動く(メニューに出ない)

        public static ParamsDoc Load(string path, List<string> errors)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                errors.Add($"params.json が見つかりません: {path}");
                return null;
            }

            ParamsDoc doc;
            try
            {
                doc = JsonUtility.FromJson<ParamsDoc>(File.ReadAllText(path));
            }
            catch (Exception e)
            {
                errors.Add($"params.json を読めません: {e.Message}");
                return null;
            }

            if (doc?.@params == null || doc.@params.Length == 0)
            {
                errors.Add("params.json の params が空です");
                return null;
            }

            var names = new HashSet<string>();
            foreach (var p in doc.@params)
            {
                if (string.IsNullOrEmpty(p.avatar) || !names.Add(p.avatar)) errors.Add($"avatar 名が空か重複しています: {p.avatar}");
                if (p.mode != "signed" && p.mode != "unsigned") errors.Add($"{p.avatar}: mode は signed / unsigned です");
            }
            foreach (var f in doc.flags ?? Array.Empty<FlagEntry>())
            {
                if (string.IsNullOrEmpty(f.avatar) || !names.Add(f.avatar)) errors.Add($"avatar 名が空か重複しています: {f.avatar}");
                if (f.type != "bool" && f.type != "int") errors.Add($"{f.avatar}: flags の type は bool か int です");
            }
            if (Array.FindIndex(doc.flags ?? Array.Empty<FlagEntry>(), f => f.avatar == ActiveFlag) < 0 ||
                Array.FindIndex(doc.flags ?? Array.Empty<FlagEntry>(), f => f.avatar == EnableFlag) < 0)
            {
                errors.Add($"flags に {ActiveFlag} と {EnableFlag} が必要です");
            }
            return errors.Count > 0 ? null : doc;
        }
    }
}
