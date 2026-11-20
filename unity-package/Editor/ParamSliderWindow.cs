using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace FlatRig.Editor
{
    /// <summary>
    /// Play モードで params.json の各パラメータをスライダーで動かし、Animator に直接入れる(docs/SPEC.md §5.8)。
    /// 平滑化後の FR_X_s を横に表示し、「0→1」で平滑化の到達時間とオーバーシュートを測る(§5.5 の受け入れ基準)。
    /// </summary>
    public class ParamSliderWindow : EditorWindow
    {
        const float SettleTolerance = 0.001f;
        const float ArriveTolerance = 0.01f;

        string paramsPath = "";
        ParamsDoc doc;
        string loadError;
        Animator animator;
        HashSet<string> animatorParams = new HashSet<string>();
        Animator paramsOf;
        readonly Dictionary<string, float> values = new Dictionary<string, float>();
        readonly Dictionary<string, int> ints = new Dictionary<string, int>();  // int の flags(FR_Expr)とジェスチャー
        readonly Dictionary<string, bool> bools = new Dictionary<string, bool>();  // FR_Active / FR_Enable 以外の bool の flags(FR_AutoBlink)
        bool active = true;
        bool enable = true;
        bool lockFps = true;
        Vector2 scroll;

        // ステップ測定: 1 = 0 に戻して落ち着くのを待つ, 2 = 1 にして到達を待つ
        ParamEntry stepParam;
        int stepPhase;
        float stepStart;
        int stepStartFrame;
        float stepMax;
        string stepResult = "";

        [MenuItem("FlatRig/Param Sliders")]
        static void Open() => GetWindow<ParamSliderWindow>("FlatRig Sliders");

        void OnEnable()
        {
            paramsPath = EditorPrefs.GetString(FlatRigWindow.ParamsPathKey, "");
            Reload();
            EditorApplication.update += Tick;
        }

        void OnDisable() => EditorApplication.update -= Tick;

        void Reload()
        {
            var errors = new List<string>();
            doc = ParamsFile.Load(paramsPath, errors);
            loadError = errors.Count > 0 ? string.Join("\n", errors) : null;
            values.Clear();
            ints.Clear();
            bools.Clear();
        }

        void OnGUI()
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                paramsPath = EditorGUILayout.TextField("params.json", paramsPath);
                if (GUILayout.Button("参照…", GUILayout.Width(60)))
                {
                    var picked = EditorUtility.OpenFilePanel("params.json を選択", File.Exists(paramsPath) ? Path.GetDirectoryName(paramsPath) : "", "json");
                    if (!string.IsNullOrEmpty(picked)) paramsPath = picked;
                }
                if (GUILayout.Button("読み込む", GUILayout.Width(70)))
                {
                    EditorPrefs.SetString(FlatRigWindow.ParamsPathKey, paramsPath);
                    Reload();
                }
            }
            animator = (Animator)EditorGUILayout.ObjectField("Animator", animator, typeof(Animator), true);
            if (animator == null && Selection.activeGameObject != null) animator = Selection.activeGameObject.GetComponentInParent<Animator>();

            if (loadError != null)
            {
                EditorGUILayout.HelpBox(loadError, MessageType.Error);
                return;
            }
            if (!Ready())
            {
                EditorGUILayout.HelpBox("Play モードにして、生成したアバター(Animator にコントローラが付いたもの)を指定してください。", MessageType.Warning);
                return;
            }

            active = EditorGUILayout.Toggle(ParamsFile.ActiveFlag, active);
            enable = EditorGUILayout.Toggle(ParamsFile.EnableFlag, enable);
            foreach (var f in doc.flags.Where(f => !f.IsInt && f.avatar != ParamsFile.ActiveFlag && f.avatar != ParamsFile.EnableFlag))
            {
                bools.TryGetValue(f.avatar, out var on);
                bools[f.avatar] = EditorGUILayout.Toggle(f.avatar, on);
            }
            foreach (var f in doc.flags.Where(f => f.IsInt))
            {
                ints.TryGetValue(f.avatar, out var n);
                ints[f.avatar] = EditorGUILayout.IntSlider(f.avatar, n, 0, Expression.MaxExpressions);
            }
            foreach (var g in new[] { AnimatorGenerator.GestureLeftParam, AnimatorGenerator.GestureRightParam })
            {
                if (!Has(g)) continue;
                ints.TryGetValue(g, out var n);
                ints[g] = EditorGUILayout.IntSlider(g, n, 0, 7);
            }
            lockFps = EditorGUILayout.Toggle("60fps に固定(測定用)", lockFps);
            EditorGUILayout.LabelField("パラメータ", "入力 / 平滑化後(_s)", EditorStyles.boldLabel);

            scroll = EditorGUILayout.BeginScrollView(scroll);
            foreach (var p in doc.@params)
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    values.TryGetValue(p.avatar, out var v);
                    using (new EditorGUI.DisabledScope(stepPhase != 0 && stepParam == p))
                    {
                        values[p.avatar] = EditorGUILayout.Slider(p.avatar, v, p.Min, 1f);
                    }
                    GUILayout.Label(Has(p.Smoothed) ? animator.GetFloat(p.Smoothed).ToString("0.000") : "—", GUILayout.Width(50));
                    if (GUILayout.Button("0→1", GUILayout.Width(45))) StartStep(p);
                }
            }
            EditorGUILayout.EndScrollView();

            if (GUILayout.Button("すべて 0 に戻す"))
            {
                stepPhase = 0;
                foreach (var p in doc.@params) values[p.avatar] = 0f;
                foreach (var k in ints.Keys.ToList()) ints[k] = 0;
            }
            if (!string.IsNullOrEmpty(stepResult)) EditorGUILayout.HelpBox(stepResult, MessageType.Info);
        }

        bool Ready() => EditorApplication.isPlaying && doc != null && animator != null && animator.runtimeAnimatorController != null;

        bool Has(string name)
        {
            if (paramsOf != animator)
            {
                paramsOf = animator;
                animatorParams = new HashSet<string>(animator.parameters.Select(x => x.name));
            }
            return animatorParams.Contains(name);
        }

        void StartStep(ParamEntry p)
        {
            stepParam = p;
            stepPhase = 1;
            values[p.avatar] = 0f;
            stepResult = $"{p.avatar}: 0 で落ち着くのを待っています…";
        }

        void Tick()
        {
            if (!Ready())
            {
                stepPhase = 0;
                return;
            }

            // 平滑化はフレーム単位なので、Editor の高いフレームレートでは実機より速く見える
            Application.targetFrameRate = lockFps ? 60 : -1;
            if (Has(ParamsFile.ActiveFlag)) animator.SetBool(ParamsFile.ActiveFlag, active);
            if (Has(ParamsFile.EnableFlag)) animator.SetBool(ParamsFile.EnableFlag, enable);
            foreach (var pair in values)
            {
                if (Has(pair.Key)) animator.SetFloat(pair.Key, pair.Value);
            }
            foreach (var pair in ints)
            {
                if (Has(pair.Key)) animator.SetInteger(pair.Key, pair.Value);
            }
            foreach (var pair in bools)
            {
                if (Has(pair.Key)) animator.SetBool(pair.Key, pair.Value);
            }

            if (stepPhase != 0 && Has(stepParam.Smoothed))
            {
                var s = animator.GetFloat(stepParam.Smoothed);
                if (stepPhase == 1 && Mathf.Abs(s) <= SettleTolerance)
                {
                    stepPhase = 2;
                    stepStart = Time.time;
                    stepStartFrame = Time.frameCount;
                    stepMax = s;
                    values[stepParam.avatar] = 1f;
                }
                else if (stepPhase == 2)
                {
                    stepMax = Mathf.Max(stepMax, s);
                    if (Mathf.Abs(s - 1f) <= ArriveTolerance)
                    {
                        stepPhase = 0;
                        var overshoot = stepMax > 1f + SettleTolerance ? $"あり(最大 {stepMax:0.000})" : "なし";
                        stepResult = $"{stepParam.avatar}: 0→1 が誤差 {ArriveTolerance} 以内に達するまで {Time.time - stepStart:0.00} 秒" +
                                     $"・{Time.frameCount - stepStartFrame} フレーム(目安 60fps で約 0.2 秒・12 フレーム)。オーバーシュート {overshoot}。";
                    }
                }
            }
            Repaint();
        }
    }
}
