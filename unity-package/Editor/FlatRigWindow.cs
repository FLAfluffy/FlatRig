using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace FlatRig.Editor
{
    /// <summary>メニュー「FlatRig > Build From Layers」の生成ウィンドウ(メッシュ生成とアニメータ生成)。</summary>
    public class FlatRigWindow : EditorWindow
    {
        const string PrefPrefix = "FlatRig.Build.";
        public const string ParamsPathKey = "FlatRig.ParamsPath";
        static readonly int[] AtlasSizes = { 1024, 2048, 4096 };
        // VRChat の GestureLeft/Right の値(0 は「使わない」として扱う)
        static readonly string[] GestureNames =
            { "使わない", "Fist(握る)", "HandOpen(開く)", "FingerPoint(指さし)", "Victory(ピース)", "RockNRoll", "HandGun", "ThumbsUp(グッド)" };
        static readonly int[] GestureValues = { 0, 1, 2, 3, 4, 5, 6, 7 };

        string layersPath = "";
        string paramsPath = "";
        readonly RigSettings settings = new RigSettings();
        readonly AnimatorSettings animatorSettings = new AnimatorSettings();
        Vector2 scroll;
        bool motionFoldout = true;
        bool exprFoldout = true;
        string[] exprNames = new string[0];  // 生成済みのリグの表情(layers.json の写しから。ExprNames)
        string exprNamesKey = "";
        bool orderFoldout;
        ReorderableList orderList;
        List<string> orderItems = new List<string>();     // 表示順の一覧。上 = 手前(PSD のレイヤーパネルと同じ向き)
        List<string> orderDefaults = new List<string>();  // 既定の順(奥 → 手前)
        readonly Dictionary<string, string> orderLabels = new Dictionary<string, string>();
        string orderKey = "";

        // 動きの大きさ: (表示名, EditorPrefs のキー, 取得, 設定)
        (string label, string key, Func<float> get, Action<float> set)[] MotionFields => new (string, string, Func<float>, Action<float>)[]
        {
            ("全体", "MotionAll", () => animatorSettings.MotionAll, v => animatorSettings.MotionAll = v),
            ("顔パーツのずれ(顔の向き)", "MotionHeadTurn", () => animatorSettings.MotionHeadTurn, v => animatorSettings.MotionHeadTurn = v),
            ("髪のずれ(顔の向き)", "MotionHair", () => animatorSettings.MotionHair, v => animatorSettings.MotionHair = v),
            ("首のかしげ", "MotionHeadTilt", () => animatorSettings.MotionHeadTilt, v => animatorSettings.MotionHeadTilt = v),
            ("体の揺れ", "MotionBody", () => animatorSettings.MotionBody, v => animatorSettings.MotionBody = v),
            ("目線", "MotionEyes", () => animatorSettings.MotionEyes, v => animatorSettings.MotionEyes = v),
            ("眉", "MotionBrows", () => animatorSettings.MotionBrows, v => animatorSettings.MotionBrows = v),
            ("鼻", "MotionNose", () => animatorSettings.MotionNose, v => animatorSettings.MotionNose = v),
            ("口の横幅", "MotionMouth", () => animatorSettings.MotionMouth, v => animatorSettings.MotionMouth = v),
            ("手の動く範囲(カメラ)", "MotionHands", () => animatorSettings.MotionHands, v => animatorSettings.MotionHands = v),
        };

        [MenuItem("FlatRig/Build From Layers")]
        static void Open() => GetWindow<FlatRigWindow>("FlatRig");

        void OnEnable()
        {
            layersPath = EditorPrefs.GetString(PrefPrefix + "LayersPath", "");
            paramsPath = EditorPrefs.GetString(ParamsPathKey, "");
            settings.RigName = EditorPrefs.GetString(PrefPrefix + "RigName", settings.RigName);
            settings.CanvasHeight = EditorPrefs.GetFloat(PrefPrefix + "CanvasHeight", settings.CanvasHeight);
            settings.BottomY = EditorPrefs.GetFloat(PrefPrefix + "BottomY", settings.BottomY);
            settings.MaxAtlasSize = EditorPrefs.GetInt(PrefPrefix + "MaxAtlasSize", settings.MaxAtlasSize);
            settings.Limbs = EditorPrefs.GetBool(PrefPrefix + "Limbs", settings.Limbs);
            settings.FreezeArms = EditorPrefs.GetBool(PrefPrefix + "FreezeArmsV2", settings.FreezeArms);
            settings.FreezeLegs = EditorPrefs.GetBool(PrefPrefix + "FreezeLegs", settings.FreezeLegs);
            settings.HandCam = EditorPrefs.GetBool(PrefPrefix + "HandCam", settings.HandCam);
            animatorSettings.SmoothK = EditorPrefs.GetFloat(PrefPrefix + "SmoothK", animatorSettings.SmoothK);
            animatorSettings.EyeLashClosedScale = EditorPrefs.GetFloat(PrefPrefix + "EyeLashClosedScale", animatorSettings.EyeLashClosedScale);
            animatorSettings.EyeHighlightFollow = EditorPrefs.GetFloat(PrefPrefix + "EyeHighlightFollow", animatorSettings.EyeHighlightFollow);
            animatorSettings.MouthClosedHold = EditorPrefs.GetFloat(PrefPrefix + "MouthClosedHold", animatorSettings.MouthClosedHold);
            foreach (var f in MotionFields) f.set(EditorPrefs.GetFloat(PrefPrefix + f.key, f.get()));
            animatorSettings.AutoExprThreshold = EditorPrefs.GetFloat(PrefPrefix + "AutoExprThreshold", animatorSettings.AutoExprThreshold);
            exprNamesKey = "";
        }

        void OnGUI()
        {
            // 項目が増えてもウィンドウの下に隠れないようにスクロールさせる
            scroll = EditorGUILayout.BeginScrollView(scroll);
            // 項目名が長いので、名前の欄を広げる(既定の 150px では「手をカメラで動かす(初期値)」などが切れる)
            var labelWidth = EditorGUIUtility.labelWidth;
            EditorGUIUtility.labelWidth = Mathf.Clamp(position.width * 0.45f, 150f, 280f);
            DrawSections();
            EditorGUIUtility.labelWidth = labelWidth;
            EditorGUILayout.EndScrollView();
        }

        void DrawSections()
        {
            EditorGUILayout.LabelField("1. レイヤー素材から平面アバターを生成", EditorStyles.boldLabel);
            layersPath = PathField("layers.json", layersPath);
            settings.RigName = EditorGUILayout.TextField("アバター名", settings.RigName);
            settings.CanvasHeight = EditorGUILayout.FloatField("キャンバスの高さ (m)", settings.CanvasHeight);
            settings.BottomY = EditorGUILayout.FloatField("キャンバス下端の高さ (m)", settings.BottomY);
            settings.MaxAtlasSize = EditorGUILayout.IntPopup("アトラスの最大サイズ", settings.MaxAtlasSize,
                new[] { "1024", "2048", "4096" }, AtlasSizes);
#if FLATRIG_VRCSDK
            settings.Limbs = EditorGUILayout.Toggle("手足をトラッキングに合わせる", settings.Limbs);
            if (settings.Limbs)
            {
                EditorGUILayout.HelpBox("腕・脚の部品(arm_l_upper など)を、VRChat の体の動き(デスクトップ・VR・フルトラ)に合わせて回します。" +
                    "アバターは Humanoid になります。Scene では T ポーズに見えますが、VRChat では立ちポーズになります。" +
                    "手足の部品が無い場合は、これまでどおり作ります。", MessageType.None);
                EditorGUI.indentLevel++;
                settings.FreezeArms = EditorGUILayout.Toggle("腕を固定(初期値)", settings.FreezeArms);
                settings.FreezeLegs = EditorGUILayout.Toggle("脚を固定(初期値)", settings.FreezeLegs);
                settings.HandCam = EditorGUILayout.Toggle("手をカメラで動かす(初期値)", settings.HandCam);
                EditorGUI.indentLevel--;
                EditorGUILayout.HelpBox("アバターを着たときの、Expression Menu の各項目の状態です。固定すると絵の元の姿勢に戻ります。" +
                    "「手をカメラで動かす」が ON なら、VTube Studio が手を認識している間は、固定中でも腕が手の位置に動きます。", MessageType.None);
            }
#endif
            DrawOrderSection();
            EditorGUILayout.HelpBox($"{RigBuilder.OutputFolder(settings.RigName)} に保存し、Scene に配置します。", MessageType.None);
            if (GUILayout.Button("アバターを生成", GUILayout.Height(28))) Build();

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("2. アニメータを生成", EditorStyles.boldLabel);
            paramsPath = PathField("params.json", paramsPath);
            animatorSettings.SmoothK = EditorGUILayout.Slider("追従の速さ (k)", animatorSettings.SmoothK, 0.05f, 1f);
            EditorGUILayout.HelpBox("1 フレームで目標値に近づく割合です。小さいほど滑らかになり、動きは遅れます。1 で平滑化しません。", MessageType.None);
            animatorSettings.EyeLashClosedScale = EditorGUILayout.Slider("閉じた目のまつ毛の太さ", animatorSettings.EyeLashClosedScale, 0.1f, 1f);
            animatorSettings.EyeHighlightFollow = EditorGUILayout.Slider("ハイライトがまぶたに押されて下がる量", animatorSettings.EyeHighlightFollow, 0f, 1f);
            animatorSettings.MouthClosedHold = EditorGUILayout.Slider("閉じた口の絵を保つ開き具合", animatorSettings.MouthClosedHold, 0f, 0.4f);
            EditorGUILayout.HelpBox("口を絵の差し替え(閉じ・半開き・開き)で動かす場合だけ使います。開き具合がこの値以下の間は、閉じた口の絵を表示します(0.1 = 10%)。", MessageType.None);
            motionFoldout = EditorGUILayout.Foldout(motionFoldout, "動きの大きさ(倍率)", true);
            if (motionFoldout)
            {
                EditorGUI.indentLevel++;
                foreach (var f in MotionFields) f.set(EditorGUILayout.Slider(f.label, f.get(), 0f, f.key == "MotionEyes" ? 1.5f : 3f));
                EditorGUI.indentLevel--;
                using (new EditorGUILayout.HorizontalScope())
                {
                    GUILayout.FlexibleSpace();
                    if (GUILayout.Button("すべて 1 に戻す", GUILayout.Width(120))) foreach (var f in MotionFields) f.set(1f);
                }
                EditorGUILayout.HelpBox("「アニメータを生成」を押し直すと反映されます。大きくするほど、パーツの裏の描かれていない部分が見えやすくなります。" +
                    "目線を 1 より大きくすると、黒目が白目からはみ出すことがあります。", MessageType.None);
            }
            DrawExpressions();
            if (GUILayout.Button("アニメータを生成", GUILayout.Height(28))) GenerateAnimator();

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("3. VRChat の設定を生成", EditorStyles.boldLabel);
#if FLATRIG_VRCSDK
            EditorGUILayout.HelpBox("2 と同じ params.json から Expression Parameters / Menu を作り、VRC Avatar Descriptor(FX・視点位置)を設定します。", MessageType.None);
            if (GUILayout.Button("VRChat 設定を生成", GUILayout.Height(28))) GenerateExpressions();
#else
            EditorGUILayout.HelpBox("VRChat SDK(Avatars)が見つかりません。VRChat Creator Companion で作成したプロジェクトで開いてください。", MessageType.Warning);
#endif
        }

        /// <summary>表情ごとのジェスチャーと自動の表情。表情の名前は「生成」したリグの layers.json から読む。</summary>
        void DrawExpressions()
        {
            var names = ExprNames();
            exprFoldout = EditorGUILayout.Foldout(exprFoldout, names.Length > 0 ? $"表情({names.Length} 個)" : "表情", true);
            if (!exprFoldout) return;
            EditorGUI.indentLevel++;
            if (names.Length == 0)
            {
                EditorGUILayout.HelpBox("このアバターには表情がありません。FlatRigPSD の部品の確認画面で表情を追加して書き出し、1 の「アバターを生成」をやり直してください。", MessageType.None);
                EditorGUI.indentLevel--;
                return;
            }
            foreach (var name in names)
            {
                var key = GestureKey(name);
                var g = EditorPrefs.GetInt(key, 0);
                var picked = EditorGUILayout.IntPopup($"「{name}」のジェスチャー", g, GestureNames, GestureValues);
                if (picked != g) EditorPrefs.SetInt(key, picked);
            }
            var autoNames = new[] { "使わない" }.Concat(names).ToArray();
            var auto = EditorPrefs.GetString(AutoKey, "");
            var index = Math.Max(0, Array.IndexOf(names, auto) + 1);
            var pickedIndex = EditorGUILayout.Popup("口の形で自動に切り替える表情", index, autoNames);
            if (pickedIndex != index) EditorPrefs.SetString(AutoKey, pickedIndex == 0 ? "" : names[pickedIndex - 1]);
            using (new EditorGUI.DisabledScope(pickedIndex == 0))
            {
                animatorSettings.AutoExprThreshold = EditorGUILayout.Slider("自動に切り替えるしきい値", animatorSettings.AutoExprThreshold, 0.2f, 0.95f);
            }
            EditorGUI.indentLevel--;
            EditorGUILayout.HelpBox("表情は、Expression Menu の「表情」か、左右どちらかの手のジェスチャーで切り替わります(優先順: メニュー → 左手 → 右手)。" +
                "自動の表情は、トラッキングの口角の上がり具合がしきい値を超えると切り替わります。変えたら「アニメータを生成」を押し直してください。", MessageType.None);
        }

        string DrawOrderPrefKey => $"{PrefPrefix}DrawOrder.{settings.RigName}";

        /// <summary>保存した表示順(奥 → 手前)。無ければ空(PSD の重なり順)。</summary>
        string[] SavedDrawOrder()
        {
            var v = EditorPrefs.GetString(DrawOrderPrefKey, "");
            return v.Length == 0 ? new string[0] : v.Split('\n');
        }

        /// <summary>パーツの表示順の並べ替え(§5.12)。腕・手の手前用の複製(動かしている間だけ表示)も並べ替えられる。</summary>
        void DrawOrderSection()
        {
            orderFoldout = EditorGUILayout.Foldout(orderFoldout, "表示順", true);
            if (!orderFoldout) return;
            RefreshDrawOrder();
            if (orderList == null)
            {
                EditorGUILayout.HelpBox("layers.json を選ぶと、パーツの表示順を変えられます。", MessageType.None);
                return;
            }
            orderList.DoLayoutList();
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                if (GUILayout.Button("PSD の順に戻す", GUILayout.Width(120)))
                {
                    EditorPrefs.DeleteKey(DrawOrderPrefKey);
                    orderKey = "";
                }
            }
            EditorGUILayout.HelpBox("上ほど手前に描きます。ドラッグで並べ替え、「アバターを生成」を押し直すと反映されます。" +
                "「(動かしている間)」は、腕・手を動かしている間だけ元の絵と入れ替えて表示する絵です(腕の固定中は元の位置の絵を表示します)。" +
                "右側は PSD のレイヤー名です。目・眉・口・鼻など、1 つにまとまった部品の中の順は PSD で変えてください。", MessageType.None);
        }

        /// <summary>選んだ layers.json・手足の設定・名前が変わったときだけ一覧を作り直す。</summary>
        void RefreshDrawOrder()
        {
            var full = File.Exists(layersPath) ? Path.GetFullPath(layersPath) : "";
            var key = full.Length == 0 ? "" : $"{full}|{File.GetLastWriteTimeUtc(full).Ticks}|{settings.Limbs}|{settings.RigName}";
            if (key == orderKey && (orderList != null || key.Length == 0)) return;
            orderKey = key;
            orderList = null;
            if (full.Length == 0) return;
            var doc = LayersImporter.LoadDoc(full, new List<string>(), out _);
            if (doc?.layers == null) return;
            var ordered = doc.layers.OrderBy(l => l.order).ToArray();
            // 手前用の複製は手足の設定が ON のときだけ。上腕(arm_?_upper)の無い腕には作らない
            var fronts = settings.Limbs
                ? DrawOrder.FrontIndices(ordered, l => LimbRig.IsArmRole(l.role) && ordered.Any(o => o.role == ArmRootRole(l.role)))
                : new int[0];
            orderDefaults = DrawOrder.DefaultKeys(ordered, fronts);
            orderItems = DrawOrder.Merge(orderDefaults, SavedDrawOrder());
            orderItems.Reverse();

            var sources = DrawOrder.PsdSources(full);
            orderLabels.Clear();
            foreach (var l in ordered)
            {
                var label = sources.TryGetValue(l.name, out var src) && src.Length > 0 ? $"{l.name}    {string.Join("・", src)}" : l.name;
                orderLabels[l.name] = label;
                orderLabels[DrawOrder.FrontKey(l.name)] = $"{l.name}(動かしている間)";
            }
            orderList = new ReorderableList(orderItems, typeof(string), true, false, false, false)
            {
                drawElementCallback = (rect, i, active, focused) =>
                    EditorGUI.LabelField(rect, orderLabels.TryGetValue(orderItems[i], out var t) ? t : orderItems[i]),
                onReorderCallback = _ => SaveDrawOrder(),
            };
        }

        static string ArmRootRole(string role)
        {
            var side = role.StartsWith("hand_") ? role.Substring(5) : role.Split('_')[1];
            return $"arm_{side}_upper";
        }

        void SaveDrawOrder()
        {
            var backToFront = Enumerable.Reverse(orderItems).ToList();
            if (backToFront.SequenceEqual(orderDefaults)) EditorPrefs.DeleteKey(DrawOrderPrefKey);
            else EditorPrefs.SetString(DrawOrderPrefKey, string.Join("\n", backToFront));
        }

        string GestureKey(string exprName) => $"{PrefPrefix}ExprGesture.{settings.RigName}.{exprName}";
        string AutoKey => $"{PrefPrefix}AutoExpr.{settings.RigName}";

        /// <summary>生成済みのリグの表情の名前。layers.json の写しが変わったときだけ読み直す。</summary>
        string[] ExprNames()
        {
            var path = RigBuilder.ToFullPath($"{RigBuilder.OutputFolder(settings.RigName)}/layers.json");
            var key = File.Exists(path) ? $"{path}|{File.GetLastWriteTimeUtc(path).Ticks}" : path;
            if (key == exprNamesKey) return exprNames;
            exprNamesKey = key;
            var doc = File.Exists(path) ? LayersImporter.LoadDoc(path, new List<string>(), out _) : null;
            exprNames = doc == null ? new string[0] : Expression.Names(doc).Take(Expression.MaxExpressions).ToArray();
            return exprNames;
        }

        /// <summary>保存したジェスチャー・自動の表情を、アニメータの設定に入れる。</summary>
        void ApplyExprPrefs()
        {
            animatorSettings.ExprGestures = ExprNames().ToDictionary(n => n, n => EditorPrefs.GetInt(GestureKey(n), 0));
            animatorSettings.AutoExpr = EditorPrefs.GetString(AutoKey, "");
        }

        static string PathField(string label, string value)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                value = EditorGUILayout.TextField(label, value);
                if (GUILayout.Button("参照…", GUILayout.Width(60)))
                {
                    var dir = File.Exists(value) ? Path.GetDirectoryName(value) : "";
                    var picked = EditorUtility.OpenFilePanel($"{label} を選択", dir, "json");
                    if (!string.IsNullOrEmpty(picked)) value = picked;
                }
            }
            return value;
        }

        void SavePrefs()
        {
            EditorPrefs.SetString(PrefPrefix + "LayersPath", layersPath);
            EditorPrefs.SetString(ParamsPathKey, paramsPath);
            EditorPrefs.SetString(PrefPrefix + "RigName", settings.RigName);
            EditorPrefs.SetFloat(PrefPrefix + "CanvasHeight", settings.CanvasHeight);
            EditorPrefs.SetFloat(PrefPrefix + "BottomY", settings.BottomY);
            EditorPrefs.SetInt(PrefPrefix + "MaxAtlasSize", settings.MaxAtlasSize);
            EditorPrefs.SetBool(PrefPrefix + "Limbs", settings.Limbs);
            EditorPrefs.SetBool(PrefPrefix + "FreezeArmsV2", settings.FreezeArms);
            EditorPrefs.SetBool(PrefPrefix + "FreezeLegs", settings.FreezeLegs);
            EditorPrefs.SetBool(PrefPrefix + "HandCam", settings.HandCam);
            EditorPrefs.SetFloat(PrefPrefix + "SmoothK", animatorSettings.SmoothK);
            EditorPrefs.SetFloat(PrefPrefix + "EyeLashClosedScale", animatorSettings.EyeLashClosedScale);
            EditorPrefs.SetFloat(PrefPrefix + "EyeHighlightFollow", animatorSettings.EyeHighlightFollow);
            EditorPrefs.SetFloat(PrefPrefix + "MouthClosedHold", animatorSettings.MouthClosedHold);
            foreach (var f in MotionFields) EditorPrefs.SetFloat(PrefPrefix + f.key, f.get());
            EditorPrefs.SetFloat(PrefPrefix + "AutoExprThreshold", animatorSettings.AutoExprThreshold);
        }

        void Build()
        {
            SavePrefs();
            settings.DrawOrder = SavedDrawOrder();
            var errors = new List<string>();
            var warnings = new List<string>();
            var root = RigBuilder.Build(layersPath, settings, errors, warnings);
            if (Report(errors, warnings, root != null))
            {
                Debug.Log($"[FlatRig] アバターを生成しました: {root.name}({RigBuilder.OutputFolder(settings.RigName)})");
            }
        }

        void GenerateAnimator()
        {
            SavePrefs();
            var errors = new List<string>();
            var warnings = new List<string>();
            var root = FindRig(errors);
            ApplyExprPrefs();
            var controller = root != null ? AnimatorGenerator.Generate(root, paramsPath, animatorSettings.Scaled(), errors, warnings) : null;
            if (Report(errors, warnings, controller != null))
            {
                Debug.Log($"[FlatRig] アニメータを生成しました: {AnimatorGenerator.ControllerPath(settings.RigName)}");
            }
        }

#if FLATRIG_VRCSDK
        void GenerateExpressions()
        {
            SavePrefs();
            var errors = new List<string>();
            var warnings = new List<string>();
            var root = FindRig(errors);
            var descriptor = root != null ? ExpressionAssets.Generate(root, paramsPath, errors, warnings) : null;
            if (Report(errors, warnings, descriptor != null))
            {
                var bits = BudgetValidator.SyncedBits(ParamsFile.Load(paramsPath, new List<string>()));
                Debug.Log($"[FlatRig] VRChat 設定を生成しました(FlatRig の同期パラメータ {bits} / {BudgetValidator.MaxBits} bit)");
            }
        }
#endif

        GameObject FindRig(List<string> errors)
        {
            var root = SceneManager.GetActiveScene().GetRootGameObjects().FirstOrDefault(g => g.name == settings.RigName);
            if (root == null) errors.Add($"Scene に「{settings.RigName}」がありません。先に 1 の「アバターを生成」を実行してください");
            return root;
        }

        static bool Report(List<string> errors, List<string> warnings, bool ok)
        {
            foreach (var w in warnings) Debug.LogWarning($"[FlatRig] {w}");
            if (ok) return true;
            foreach (var e in errors) Debug.LogError($"[FlatRig] {e}");
            EditorUtility.DisplayDialog("FlatRig", "生成できませんでした。Console のエラーを確認してください。", "OK");
            return false;
        }
    }
}
