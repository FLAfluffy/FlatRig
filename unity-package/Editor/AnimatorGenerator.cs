using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using Object = UnityEngine.Object;

namespace FlatRig.Editor
{
    /// <summary>動きの振幅などの初期値(docs/SPEC.md §5.4)。目視で調整する前提。</summary>
    public class AnimatorSettings
    {
        public float SmoothK = 0.32f;         // 1 フレームで目標に近づく割合(60fps で約 0.2 秒で誤差 1% 以内)
        public float ParallaxX = 0.03f;       // depth 1 あたりの左右の視差(キャンバス幅比)
        public float ParallaxY = 0.02f;       // depth 1 あたりの上下の視差(キャンバス高さ比)
        public float HeadRollDeg = 10f;
        public float BodyX = 0.02f;           // キャンバス幅比
        public float BodyY = 0.01f;           // キャンバス高さ比
        public float BodyRollDeg = 4f;
        public float EyeClosedScale = 0.05f;  // 旧方式(rig.json にまばたきの寸法が無いとき)の閉じた目の縦の縮尺
        public float EyeLashClosedScale = EyeLid.DefaultLashClosedScale;  // 閉じた目の、まつ毛の縦の縮尺
        public float EyeHighlightFollow = EyeLid.DefaultHighlightFollow;  // 瞳のハイライトがまぶたに押されて下がれる量(黒目の下端までの余白に対する割合)
        public float EyeBallRange = 0.8f;     // 白目と黒目の余白に対する移動幅
        public float BrowY = 0.02f;           // キャンバス高さ比
        public float MouthFormScale = 0.3f;   // 口の横幅の増減
        public float MouthClosedHold = 0.1f;  // 口の絵の差し替えで、開き具合がこれ以下なら最初の絵(閉じた口)のままにする(MouthPictureOpen)
        public float BreathAmplitude = 0.004f;  // m
        public float IdleLoopSec = 4f;
        // 自動まばたき(FR_AutoBlink、docs/SPEC.md §5.5)。この長さのループの中の各時刻で両目を 1 回ずつ閉じる(不規則に見えるように間をばらつかせる)
        public float AutoBlinkLoopSec = 10f;
        public float[] AutoBlinkTimes = { 2.5f, 6.0f, 7.2f };
        public float TransitionSec = 0.25f;

        // 表情(docs/SPEC.md §5.9)。表情の無いアバターでは使わない
        public Dictionary<string, int> ExprGestures = new Dictionary<string, int>();  // 表情の名前 → ハンドジェスチャー(GestureLeft/Right の値。0 = 使わない)
        public string AutoExpr = "";            // トラッキングの口の形(FR_MouthForm)が大きいと自動でなる表情の名前(空 = 使わない)
        public float AutoExprThreshold = 0.5f;  // 自動の表情にする FR_MouthForm(平滑化後)の値。これより 0.15 下がると戻る
        public float ExprFadeSec = 0.15f;       // 表情を溶かして入れ替える時間

        // 動きの大きさの倍率(FlatRig ウィンドウで調整。1 = 上の初期値のまま)
        public float MotionAll = 1f;
        public float MotionHeadTurn = 1f;   // 顔の向き(髪以外の頭のパーツの視差)
        public float MotionHair = 1f;       // 顔の向き(前髪・横髪・後ろ髪の視差。role が *_hair)
        public float MotionHeadTilt = 1f;   // 首のかしげ
        public float MotionBody = 1f;       // 体の揺れ
        public float MotionEyes = 1f;       // 目線
        public float MotionBrows = 1f;      // 眉
        public float MotionMouth = 1f;      // 口の横幅
        public float MotionNose = 1f;       // 鼻(role = nose)の顔の向きでの動き。「顔の向き」「全体」にさらに掛ける
        public float MotionHands = 1f;      // 手をカメラで動かすときの、手の動く範囲(§5.11)

        /// <summary>倍率を掛けた値の設定を返す(生成にはこちらを使う)。</summary>
        public AnimatorSettings Scaled()
        {
            var s = (AnimatorSettings)MemberwiseClone();
            s.ParallaxX = ParallaxX * MotionAll;  // 顔パーツ・髪の倍率はレイヤーごとに掛ける(ParallaxScale)
            s.ParallaxY = ParallaxY * MotionAll;
            s.HeadRollDeg = HeadRollDeg * MotionAll * MotionHeadTilt;
            s.BodyX = BodyX * MotionAll * MotionBody;
            s.BodyY = BodyY * MotionAll * MotionBody;
            s.BodyRollDeg = BodyRollDeg * MotionAll * MotionBody;
            s.EyeBallRange = EyeBallRange * MotionAll * MotionEyes;
            s.BrowY = BrowY * MotionAll * MotionBrows;
            s.MouthFormScale = MouthFormScale * MotionAll * MotionMouth;
            return s;
        }
    }

    /// <summary>
    /// params.json と layers.json から AnimatorController(FX 用)を生成する(docs/SPEC.md §5.5)。
    /// レイヤー:
    ///   FR_Smoothing  Direct ブレンドツリーで各 FR_X_s ← lerp(FR_X_s, FR_X, FR_SmoothK) を毎フレーム計算
    ///   FR_AutoBlink  Off / On の 2 状態(params.json に FR_AutoBlink があるときだけ)。On は自動まばたきの開き具合を
    ///                 FR_AutoBlinkOpen に書くループ。Tracking の目の開閉は FR_AutoBlinkW(Off 0 / On 1)で自動まばたきに切り替わる
    ///   FR_Main       Disabled / Idle / Tracking の 3 状態。Tracking は FR_X_s を読む Direct ブレンドツリー
    ///   FR_Expression Normal と表情ごとの状態(表情があるときだけ)。マテリアルの _FR_ExprA〜D だけを書く
    /// 1 つの (ボーン, プロパティ) を書くのは 1 つのブレンドツリーだけ(Direct ツリー内で値が足し合わされないように)。
    /// すべての状態は Write Defaults ON。
    /// </summary>
    public static class AnimatorGenerator
    {
        public const string OneParam = "FR_One";
        public const string SmoothKParam = "FR_SmoothK";
        public const string SmoothingLayer = "FR_Smoothing";
        public const string MainLayer = "FR_Main";
        public const string ExpressionLayer = "FR_Expression";
        public const string AutoBlinkLayer = "FR_AutoBlink";
        public const string AutoBlinkOpenParam = "FR_AutoBlinkOpen";  // 自動まばたきの開き具合(ローカル。FR_AutoBlink 層が書く)
        public const string AutoBlinkWeightParam = "FR_AutoBlinkW";   // Tracking の目の開閉を自動まばたきにする割合(ローカル。同上)
        /// <summary>FX の中だけで使う float(同期しない・Expression Parameters に入れない)。</summary>
        public static readonly string[] LocalParams = { OneParam, SmoothKParam, AutoBlinkOpenParam, AutoBlinkWeightParam };
        const float BlinkCloseSec = 0.06f;  // まばたきで閉じきるまで
        const float BlinkOpenSec = 0.08f;   // 閉じてから開ききるまで
        public const string GestureLeftParam = "GestureLeft";    // VRChat の組み込みパラメータ(同期ビットを使わない)
        public const string GestureRightParam = "GestureRight";
        public const float AutoExprHysteresis = 0.15f;
        public const string FreezeArmsParam = "FR_FreezeArms";  // 腕を固定(Expression Menu。手足の設定が ON のときだけ)
        public const string FreezeLegsParam = "FR_FreezeLegs";  // 脚を固定(同上)
        public static readonly string[] FreezeParams = { FreezeArmsParam, FreezeLegsParam };
        public const string HandCamParam = "FR_HandCam";        // 手をカメラで動かす(Expression Menu。§5.11)
        /// <summary>手の追従でブリッジが送るパラメータ。side は "R"(キャラクターの右手 = 画面左の腕 arm_l)/ "L"。</summary>
        public static string HandXParam(string side) => $"FR_Hand{side}X";      // 外側が +1(VTS の HandRightPositionX など / 10)
        public static string HandYParam(string side) => $"FR_Hand{side}Y";      // 上が +1
        public static string HandFoundParam(string side) => $"FR_Hand{side}Found";
        static readonly (string side, string limb)[] HandSides = { ("R", "arm_l"), ("L", "arm_r") };
        /// <summary>params.json の外で FX と Expression Parameters に足すパラメータ(手足の固定・手の追従)。</summary>
        public static readonly string[] ExtraParams =
        {
            FreezeArmsParam, FreezeLegsParam, HandCamParam,
            HandXParam("R"), HandYParam("R"), HandFoundParam("R"), HandXParam("L"), HandYParam("L"), HandFoundParam("L"),
        };
        const int HandGrid = 9;                 // 手の位置のブレンドツリーの格子(9 × 9 点)
        const float HandRangePerArm = 1.2f;     // 手の位置 ±1 に当たる、頭からの距離(腕の長さの倍数。MotionHands を掛ける)
        const float HandFadeSec = 0.1f;

        public static string ControllerPath(string rigName) => $"{RigBuilder.OutputFolder(rigName)}/{rigName}_FX.controller";

        public static AnimatorController Generate(GameObject root, string paramsPath, AnimatorSettings s,
            List<string> errors, List<string> warnings)
        {
            var folder = RigBuilder.OutputFolder(root.name);
            var layers = LayersImporter.LoadDoc(RigBuilder.ToFullPath($"{folder}/layers.json"), errors, out _);
            var info = LoadRigInfo(RigBuilder.ToFullPath($"{folder}/rig.json"), errors);
            var doc = ParamsFile.Load(paramsPath, errors);
            if (errors.Count > 0) return null;

            var rig = new Rig(root, layers, info);
            if (!rig.Validate(errors)) return null;

            var path = ControllerPath(root.name);
            var controller = PrepareController(path);
            var b = new Builder(controller, root.transform);

            AddParameters(controller, doc, s);
            BuildSmoothingLayer(b, controller, doc);
            var autoBlink = BuildAutoBlinkLayer(b, controller, rig, doc, s, warnings);
            BuildMainLayer(b, controller, rig, doc, s, autoBlink, warnings);
            BuildExpressionLayer(b, controller, rig, doc, s, warnings);  // Idle / Disabled の中立クリップに表情の重みを入れないよう最後に作る
            BuildFreezeLayers(b, controller, root.transform, info, s);
            var handConditions = BuildHandLayers(b, controller, root.transform, rig, doc, s, warnings);
            BuildArmFrontLayers(b, controller, rig, handConditions);

            var animator = root.GetComponent<Animator>();
            if (animator == null) animator = root.AddComponent<Animator>();
            animator.runtimeAnimatorController = controller;  // Play モードでの確認用(VRChat では Descriptor の FX に設定)
            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();
            return controller;
        }

        // --- FR_FreezeArms / FR_FreezeLegs ---------------------------------------------------------

        /// <summary>
        /// 手足の固定(§5.10)。腕・脚ごとに Bool のパラメータと層を作り、ON で関節ノードの Constraint の重みを 0 にする
        /// (関節ノードは絵の初期の向きに戻る)。手足の Constraint が無ければ作らない。初期値は rig.json。
        /// </summary>
        static void BuildFreezeLayers(Builder b, AnimatorController controller, Transform root, RigInfo info, AnimatorSettings s)
        {
            foreach (var (param, arms, frozen) in new[] { (FreezeArmsParam, true, info.arms_frozen), (FreezeLegsParam, false, info.legs_frozen) })
            {
                var targets = LimbRig.FreezeTargets(root, arms);
                if (targets.Count == 0) continue;
                controller.AddParameter(new AnimatorControllerParameter
                {
                    name = param, type = AnimatorControllerParameterType.Bool, defaultBool = frozen,
                });
                var followClip = b.Clip($"{param} OFF");
                var frozenClip = b.Clip($"{param} ON");
                foreach (var (node, type) in targets)
                {
                    b.ComponentFloat(followClip, node, type, "GlobalWeight", 1f);
                    b.ComponentFloat(frozenClip, node, type, "GlobalWeight", 0f);
                }
                var sm = b.AddLayer(param);
                var follow = AddState(sm, "Follow", followClip, new Vector3(300, 100, 0));
                var hold = AddState(sm, "Frozen", frozenClip, new Vector3(300, 200, 0));
                sm.defaultState = frozen ? hold : follow;
                Transition(follow, hold, s, (AnimatorConditionMode.If, param));
                Transition(hold, follow, s, (AnimatorConditionMode.IfNot, param));
            }
        }

        // --- FR_HandR / FR_HandL --------------------------------------------------------------

        /// <summary>
        /// 手をカメラで動かす(§5.11)。腕ごとに Off / On の層を作る。On は FR_HandCam・FR_Hand{side}Found・FR_Active・FR_Enable が
        /// すべて true のとき。On の間は腕と手の関節ノードの Constraint を止め(IsActive = 0。「腕を固定」より優先)、
        /// 手の位置(FR_Hand{side}X / Y)から 2 関節 IK で求めた上腕・前腕の回転を、格子状の 2D ブレンドツリーで書く。
        /// 手は前腕に沿ったまま(回転しない)。上腕の部品と rig.json の関節が無い腕は作らない。
        /// </summary>
        static Dictionary<string, List<string>> BuildHandLayers(Builder b, AnimatorController controller, Transform root, Rig rig,
            ParamsDoc doc, AnimatorSettings s, List<string> warnings)
        {
            var result = new Dictionary<string, List<string>>();  // 腕(side)→ Camera になる条件(すべて true のとき)
            var constraints = LimbRig.JointConstraints(root);
            var limbs = rig.Info.limbs ?? new LimbInfo[0];
            var center = (Vector2)root.InverseTransformPoint(rig.Head != null ? rig.Head.position : root.position);
            var built = false;
            foreach (var (side, limbName) in HandSides)
            {
                var limb = limbs.FirstOrDefault(l => l.name == limbName);
                if (limb == null || limb.segments == null || limb.JointCount != limb.segments.Length + 1) continue;
                var upperRole = $"{limbName}_upper";
                var upperIndex = Array.IndexOf(limb.segments, upperRole);
                var upperNode = constraints.FirstOrDefault(c => c.role == upperRole).node;
                if (upperIndex != 0 || upperNode == null) continue;
                var lowerRole = $"{limbName}_lower";
                var lowerIndex = Array.IndexOf(limb.segments, lowerRole);
                var lowerNode = lowerIndex == 1 ? constraints.FirstOrDefault(c => c.role == lowerRole).node : null;
                var twoBones = lowerNode != null;

                var shoulder = limb.Joint(0);
                var elbow = limb.Joint(1);
                var wrist = twoBones ? limb.Joint(2) : elbow;
                var armLength = (elbow - shoulder).magnitude + (twoBones ? (wrist - elbow).magnitude : 0f);
                var range = armLength * HandRangePerArm * s.MotionHands;
                var outward = side == "R" ? 1f : -1f;  // キャラクターの右 = +X

                if (!built)
                {
                    controller.AddParameter(new AnimatorControllerParameter
                    {
                        name = HandCamParam, type = AnimatorControllerParameterType.Bool, defaultBool = rig.Info.hand_cam,
                    });
                    built = true;
                }
                var px = HandXParam(side);
                var py = HandYParam(side);
                var found = HandFoundParam(side);
                controller.AddParameter(px, AnimatorControllerParameterType.Float);
                controller.AddParameter(py, AnimatorControllerParameterType.Float);
                controller.AddParameter(found, AnimatorControllerParameterType.Bool);

                // 格子点ごとの角度(隣どうしで 360 度飛ばないように、中心から広げて巻き戻す)
                var upper = new float[HandGrid, HandGrid];
                var lower = new float[HandGrid, HandGrid];
                for (var i = 0; i < HandGrid; i++)
                for (var j = 0; j < HandGrid; j++)
                {
                    var target = center + new Vector2(outward * GridValue(i) * range, GridValue(j) * range);
                    (upper[i, j], lower[i, j]) = LimbRig.HandIK(shoulder, elbow, wrist, twoBones, outward, target);
                }
                Unwrap(upper);
                Unwrap(lower);

                var tree = b.Tree($"Hand{side} IK", BlendTreeType.FreeformCartesian2D, px, py);
                for (var i = 0; i < HandGrid; i++)
                for (var j = 0; j < HandGrid; j++)
                {
                    var clip = b.Clip($"Hand{side} {GridValue(i):+0.00;-0.00} {GridValue(j):+0.00;-0.00}");
                    b.ComponentVector(clip, upperNode, typeof(Transform), "localEulerAnglesRaw", new Vector3(0f, 0f, upper[i, j]));
                    if (twoBones) b.ComponentVector(clip, lowerNode, typeof(Transform), "localEulerAnglesRaw", new Vector3(0f, 0f, lower[i, j]));
                    tree.AddChild(clip, new Vector2(GridValue(i), GridValue(j)));
                }

                // 腕と手の Constraint を止める。手は前腕に沿わせる(絵の向き)
                var roles = new[] { upperRole, lowerRole, "hand" + limbName.Substring(3) };
                var stop = b.Clip($"Hand{side} constraints off");
                foreach (var c in constraints.Where(c => roles.Contains(c.role)))
                {
                    b.ComponentFloat(stop, c.node, c.type, "IsActive", 0f);
                    if (c.role == roles[2] || (c.role == lowerRole && !twoBones))
                        b.ComponentVector(stop, c.node, typeof(Transform), "localEulerAnglesRaw", Vector3.zero);
                }
                var on = b.Tree($"Hand{side}", BlendTreeType.Direct, OneParam);
                AddDirectChild(on, tree);
                AddDirectChild(on, stop);

                var sm = b.AddLayer($"FR_Hand{side}");
                var offState = AddState(sm, "Off", b.Clip($"Hand{side} off"), new Vector3(300, 100, 0));
                var onState = AddState(sm, "Camera", on, new Vector3(300, 200, 0));
                sm.defaultState = offState;
                var conditions = new List<string> { HandCamParam, found };
                foreach (var flag in new[] { ParamsFile.ActiveFlag, ParamsFile.EnableFlag })
                {
                    if (doc.flags.Any(f => f.avatar == flag)) conditions.Add(flag);
                }
                result[side] = conditions;
                var enter = offState.AddTransition(onState);
                SetFade(enter);
                foreach (var c in conditions) enter.AddCondition(AnimatorConditionMode.If, 0f, c);
                foreach (var c in conditions)
                {
                    var exit = onState.AddTransition(offState);
                    SetFade(exit);
                    exit.AddCondition(AnimatorConditionMode.IfNot, 0f, c);
                }
            }
            if (!built && limbs.Any(l => l.name.StartsWith("arm_")))
                warnings.Add("腕の関節が見つからないため、手をカメラで動かす層を作りませんでした(1 の「アバターを生成」をやり直してください)");
            return result;
        }

        // --- FR_ArmFrontR / FR_ArmFrontL ----------------------------------------------------------

        /// <summary>
        /// 腕・手を動かしている間だけ一番手前に描く(§5.10)。生成時に腕・手の部品ごとに手前用の複製({layer}_Front、ふだんは
        /// 大きさ 0)を作ってあるので、腕ごとに Back / Front の 2 状態で元の部品と複製の大きさを入れ替える。
        /// Front は「腕を固定」が OFF のとき、または手をカメラで動かしているとき(handConditions がすべて true)。
        /// </summary>
        static void BuildArmFrontLayers(Builder b, AnimatorController controller, Rig rig, Dictionary<string, List<string>> handConditions)
        {
            var hasFreeze = controller.parameters.Any(p => p.name == FreezeArmsParam);
            foreach (var (side, limbName) in HandSides)
            {
                var roles = new[] { $"{limbName}_upper", $"{limbName}_lower", "hand" + limbName.Substring(3) };
                var pairs = rig.Doc.layers.Where(l => roles.Contains(l.role))
                    .Select(l => (back: rig.F(l), front: rig.Front(l))).Where(x => x.back != null && x.front != null).ToArray();
                handConditions.TryGetValue(side, out var camera);
                if (pairs.Length == 0 || (!hasFreeze && camera == null)) continue;

                var backClip = b.Clip($"ArmFront{side} back");
                var frontClip = b.Clip($"ArmFront{side} front");
                foreach (var (back, front) in pairs)
                {
                    b.ComponentVector(backClip, back, typeof(Transform), "m_LocalScale", Vector3.one);
                    b.ComponentVector(backClip, front, typeof(Transform), "m_LocalScale", Vector3.zero);
                    b.ComponentVector(frontClip, back, typeof(Transform), "m_LocalScale", Vector3.zero);
                    b.ComponentVector(frontClip, front, typeof(Transform), "m_LocalScale", Vector3.one);
                }
                var sm = b.AddLayer($"FR_ArmFront{side}");
                var backState = AddState(sm, "Back", backClip, new Vector3(300, 100, 0));
                var frontState = AddState(sm, "Front", frontClip, new Vector3(300, 200, 0));
                sm.defaultState = backState;

                // Back → Front: 固定 OFF、またはカメラの条件がすべて true
                if (hasFreeze) Instant(backState, frontState, (AnimatorConditionMode.IfNot, FreezeArmsParam));
                if (camera != null) Instant(backState, frontState, camera.Select(c => (AnimatorConditionMode.If, c)).ToArray());
                // Front → Back: 固定 ON(固定が無ければ常に)、かつカメラの条件のどれかが false
                var frozen = hasFreeze ? new[] { (AnimatorConditionMode.If, FreezeArmsParam) } : new (AnimatorConditionMode, string)[0];
                if (camera == null) Instant(frontState, backState, frozen);
                else foreach (var c in camera) Instant(frontState, backState, frozen.Append((AnimatorConditionMode.IfNot, c)).ToArray());
            }
        }

        static void Instant(AnimatorState from, AnimatorState to, params (AnimatorConditionMode mode, string param)[] conditions)
        {
            var t = from.AddTransition(to);
            t.hasExitTime = false;
            t.hasFixedDuration = true;
            t.duration = 0f;
            foreach (var (mode, param) in conditions) t.AddCondition(mode, 0f, param);
        }

        static float GridValue(int i) => -1f + 2f * i / (HandGrid - 1);

        static void SetFade(AnimatorStateTransition t)
        {
            t.hasExitTime = false;
            t.hasFixedDuration = true;
            t.duration = HandFadeSec;
        }

        /// <summary>格子の角度を、中心から幅優先で隣の値に近づける(±360 度)。</summary>
        static void Unwrap(float[,] v)
        {
            var n = v.GetLength(0);
            var done = new bool[n, n];
            var queue = new Queue<(int, int)>();
            var c = n / 2;
            done[c, c] = true;
            queue.Enqueue((c, c));
            while (queue.Count > 0)
            {
                var (i, j) = queue.Dequeue();
                foreach (var (di, dj) in new[] { (1, 0), (-1, 0), (0, 1), (0, -1) })
                {
                    int a = i + di, k = j + dj;
                    if (a < 0 || k < 0 || a >= n || k >= n || done[a, k]) continue;
                    v[a, k] = v[i, j] + LimbRig.Wrap180(v[a, k] - v[i, j]);
                    done[a, k] = true;
                    queue.Enqueue((a, k));
                }
            }
        }

        /// <summary>顔の向きでの視差の倍率: 髪は「髪のずれ」、それ以外は「顔パーツのずれ」(鼻はさらに「鼻」)。</summary>
        static float ParallaxScale(LayerDef l, AnimatorSettings s) =>
            l.role.EndsWith("_hair") ? s.MotionHair : s.MotionHeadTurn * (l.role == "nose" ? s.MotionNose : 1f);

        static RigInfo LoadRigInfo(string fullPath, List<string> errors)
        {
            if (!File.Exists(fullPath))
            {
                errors.Add("rig.json がありません。先に 1 の「アバターを生成」を実行してください");
                return null;
            }
            var info = JsonUtility.FromJson<RigInfo>(File.ReadAllText(fullPath));
            if (info == null || info.meters_per_px <= 0f) errors.Add("rig.json の内容を読み取れません。1 の「アバターを生成」をやり直してください");
            return info;
        }

        /// <summary>既存のコントローラは中身だけ消して作り直す(GUID を保つ)。</summary>
        static AnimatorController PrepareController(string path)
        {
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            if (controller == null) controller = AnimatorController.CreateAnimatorControllerAtPath(path);
            controller.layers = Array.Empty<AnimatorControllerLayer>();
            controller.parameters = Array.Empty<AnimatorControllerParameter>();
            foreach (var sub in AssetDatabase.LoadAllAssetsAtPath(path))
            {
                if (sub == null || sub == controller) continue;
                AssetDatabase.RemoveObjectFromAsset(sub);
                Object.DestroyImmediate(sub, true);
            }
            return controller;
        }

        static void AddParameters(AnimatorController controller, ParamsDoc doc, AnimatorSettings s)
        {
            var list = new List<AnimatorControllerParameter>
            {
                new AnimatorControllerParameter { name = OneParam, type = AnimatorControllerParameterType.Float, defaultFloat = 1f },
                new AnimatorControllerParameter { name = SmoothKParam, type = AnimatorControllerParameterType.Float, defaultFloat = s.SmoothK },
            };
            foreach (var p in doc.@params)
            {
                list.Add(new AnimatorControllerParameter { name = p.avatar, type = AnimatorControllerParameterType.Float });
                list.Add(new AnimatorControllerParameter { name = p.Smoothed, type = AnimatorControllerParameterType.Float });
            }
            foreach (var f in doc.flags)
            {
                list.Add(f.IsInt
                    ? new AnimatorControllerParameter { name = f.avatar, type = AnimatorControllerParameterType.Int }
                    : new AnimatorControllerParameter
                    {
                        name = f.avatar, type = AnimatorControllerParameterType.Bool, defaultBool = f.avatar == ParamsFile.EnableFlag,
                    });
            }
            controller.parameters = list.ToArray();
        }

        // --- FR_Smoothing ------------------------------------------------------------------------

        static void BuildSmoothingLayer(Builder b, AnimatorController controller, ParamsDoc doc)
        {
            var direct = b.Tree("Smoothing", BlendTreeType.Direct, OneParam);
            foreach (var p in doc.@params)
            {
                var lo = b.Clip($"{p.Smoothed} = -1");
                b.Param(lo, p.Smoothed, -1f);
                var hi = b.Clip($"{p.Smoothed} = +1");
                b.Param(hi, p.Smoothed, 1f);

                // 1D ツリー(-1 と +1 の 2 点)は入力値をそのまま FR_X_s に書く。
                // prev(FR_X_s)と target(FR_X)を FR_SmoothK で混ぜる → FR_X_s ← lerp(FR_X_s, FR_X, k)
                var prev = b.Tree($"{p.Smoothed} prev", BlendTreeType.Simple1D, p.Smoothed);
                prev.AddChild(lo, -1f);
                prev.AddChild(hi, 1f);
                var target = b.Tree($"{p.Smoothed} target", BlendTreeType.Simple1D, p.avatar);
                target.AddChild(lo, -1f);
                target.AddChild(hi, 1f);
                var mix = b.Tree($"{p.Smoothed} smooth", BlendTreeType.Simple1D, SmoothKParam);
                mix.AddChild(prev, 0f);
                mix.AddChild(target, 1f);
                AddDirectChild(direct, mix);
            }

            var sm = b.AddLayer(SmoothingLayer);
            var state = sm.AddState("Smooth", new Vector3(300, 100, 0));
            state.motion = direct;
            state.writeDefaultValues = true;
            sm.defaultState = state;
        }

        // --- FR_Main -----------------------------------------------------------------------------

        static void BuildMainLayer(Builder b, AnimatorController controller, Rig rig, ParamsDoc doc, AnimatorSettings s,
            bool autoBlink, List<string> warnings)
        {
            var tracking = b.Tree("Tracking", BlendTreeType.Direct, OneParam);
            var used = new HashSet<string>();
            string Use(string avatar)
            {
                var p = doc.@params.FirstOrDefault(x => x.avatar == avatar);
                if (p == null) return null;
                used.Add(avatar);
                return p.Smoothed;
            }
            void Add(Motion m)
            {
                if (m != null) AddDirectChild(tracking, m);
            }

            var w = rig.Info.canvas_width_m;
            var h = rig.Info.canvas_height_m;

            // 頭の視差: 頭のレイヤーの視差ノードを depth に比例して動かす。
            // 向きの符号は実機で確認済み(+AngleX で +X、+AngleZ で −Z 回転。SPEC §9 U17)
            var headLayers = rig.Doc.layers.Where(l => l.group == "head").ToArray();
            Add(AxisTree(b, "Head Parallax", Use("FR_AngleX"), Use("FR_AngleY"), (x, y) =>
            {
                var clip = b.Clip($"Head Parallax {x:+0;-0;0},{y:+0;-0;0}");
                foreach (var l in headLayers)
                {
                    var d = rig.ParallaxDepth(l) * ParallaxScale(l, s);
                    var t = rig.P(l);
                    b.Pos(clip, t, t.localPosition + new Vector3(d * s.ParallaxX * w * x, d * s.ParallaxY * h * y, 0f));
                }
                return clip;
            }));

            Add(AxisTree(b, "Head Roll", Use("FR_AngleZ"), null, (z, _) =>
            {
                var clip = b.Clip($"Head Roll {z:+0;-0;0}");
                b.Euler(clip, rig.Head, rig.Head.localEulerAngles + new Vector3(0f, 0f, -s.HeadRollDeg * z));
                return clip;
            }));

            Add(AxisTree(b, "Body Move", Use("FR_BodyAngleX"), Use("FR_BodyAngleY"), (x, y) =>
            {
                var clip = b.Clip($"Body Move {x:+0;-0;0},{y:+0;-0;0}");
                b.Pos(clip, rig.Body, rig.Body.localPosition + new Vector3(s.BodyX * w * x, s.BodyY * h * y, 0f));
                return clip;
            }));

            Add(AxisTree(b, "Body Roll", Use("FR_BodyAngleZ"), null, (z, _) =>
            {
                var clip = b.Clip($"Body Roll {z:+0;-0;0}");
                b.Euler(clip, rig.Body, rig.Body.localEulerAngles + new Vector3(0f, 0f, -s.BodyRollDeg * z));
                return clip;
            }));

            foreach (var side in new[] { "L", "R" })
            {
                var white = rig.Role($"eye_{side.ToLowerInvariant()}_white");
                var brow = rig.Role($"brow_{side.ToLowerInvariant()}");
                var lid = rig.Lid(side.ToLowerInvariant());
                if (white != null && lid != null)
                {
                    // まぶた: まつ毛を下げて平たくし、白目・黒目はまつ毛より上をシェーダで切り取る(EyeLid)
                    var sideLower = side.ToLowerInvariant();
                    Add(EyeOpenTree(b, $"Eye{side} Open", Use($"FR_Eye{side}Open"), autoBlink, v =>
                    {
                        var clip = b.Clip($"Eye{side} Open {v}");
                        SetEyeLid(b, clip, rig, sideLower, s, v);
                        if (rig.Role($"eye_{sideLower}_closed") != null)
                            b.MaterialFloat(clip, rig.Mesh, Expression.EyeOpenProp(sideLower), v, 1f);  // 閉じた目の絵との切り替え
                        return clip;
                    }, EyeLidBreaks(rig, sideLower, s)));
                }
                else if (white != null)
                {
                    warnings.Add($"FR_Eye{side}Open: まぶたの寸法が rig.json に無いため旧方式(縦に潰す)で閉じます。「アバターを生成」をやり直すと新方式になります");
                    var f = rig.F(white);
                    Add(EyeOpenTree(b, $"Eye{side} Open", Use($"FR_Eye{side}Open"), autoBlink, v =>
                    {
                        var clip = b.Clip($"Eye{side} Open {v}");
                        var sc = f.localScale;
                        b.Scale(clip, f, new Vector3(sc.x, sc.y * Mathf.Lerp(s.EyeClosedScale, 1f, v), sc.z));
                        return clip;
                    }));
                }
                else if (doc.@params.Any(p => p.avatar == $"FR_Eye{side}Open"))
                {
                    warnings.Add($"FR_Eye{side}Open: 白目のレイヤーが無いため動きを付けません");
                }

                if (brow != null)
                {
                    var f = rig.F(brow);
                    Add(AxisTree(b, $"Brow{side} Y", Use($"FR_Brow{side}Y"), null, (y, _) =>
                    {
                        var clip = b.Clip($"Brow{side} Y {y:+0;-0;0}");
                        b.Pos(clip, f, f.localPosition + new Vector3(0f, s.BrowY * h * y, 0f));
                        return clip;
                    }));
                }
            }

            // 黒目: 白目と黒目の大きさの差の範囲で動かす
            var pupils = new[] { "l", "r" }.Select(side => (pupil: rig.Role($"eye_{side}_pupil"), white: rig.Role($"eye_{side}_white")))
                .Where(e => e.pupil != null).ToArray();
            if (pupils.Length > 0)
            {
                Add(AxisTree(b, "EyeBall", Use("FR_EyeBallX"), Use("FR_EyeBallY"), (x, y) =>
                {
                    var clip = b.Clip($"EyeBall {x:+0;-0;0},{y:+0;-0;0}");
                    foreach (var (pupil, white) in pupils)
                    {
                        var rx = (white != null ? (white.Width - pupil.Width) * 0.5f : pupil.Width * 0.3f) * s.EyeBallRange;
                        var ry = (white != null ? (white.Height - pupil.Height) * 0.5f : pupil.Height * 0.3f) * s.EyeBallRange;
                        var f = rig.F(pupil);
                        var m = rig.Info.meters_per_px;
                        b.Pos(clip, f, f.localPosition + new Vector3(Mathf.Max(rx, 0f) * m * x, Mathf.Max(ry, 0f) * m * y, 0f));
                    }
                    return clip;
                }));
            }

            // 口の開閉。絵の差し替え(mouth_frames)では開き具合をマテリアルの _FR_MouthOpen に書き、シェーダが絵を選ぶ。
            // そうでなければ開いた口を縦に伸ばす(今まで通り)。表情の口の絵は、どちらでも _FR_MouthOpen で選ぶ(Expression)
            var frames = rig.Doc.mouth_frames;
            var exprMouth = rig.HasMesh && rig.Doc.layers.Any(l => Expression.IsMouthSlot(Expression.Slot(l)));
            var openDef = frames ? null : rig.Role("mouth_open");
            // 絵と絵の間では、2 枚を混ぜながら大きさ・位置を相手の絵に合わせる(MouthMorph)
            var mouthSets = rig.HasMesh ? Expression.MouthSets(rig.Doc) : new List<LayerDef[]>();
            var hold = MouthHold(s);
            var morphPoints = MorphPoints(mouthSets).Concat(mouthSets.Count > 0 || exprMouth ? new[] { hold, 0.5f } : new float[0])
                .Distinct().ToArray();
            if (frames && rig.HasMesh)
            {
                Add(UnsignedTree(b, "Mouth Open", Use("FR_MouthOpenY"), v =>
                {
                    var clip = b.Clip($"Mouth Open {v}");
                    var u = MouthPictureOpen(hold, v);
                    b.MaterialFloat(clip, rig.Mesh, Expression.MouthOpenProp, u);
                    foreach (var set in mouthSets) MouthMorph(b, clip, rig, set, u);
                    return clip;
                }, morphPoints));
            }
            else if (frames)
            {
                warnings.Add("口の絵の差し替え: メッシュが見つからないため口を開きません。「アバターを生成」をやり直してください");
            }
            else if (openDef != null)
            {
                var f = rig.F(openDef);
                // 口の隠しの構造(閉じた口の肌で口内を覆う)では、開き具合が MouthCoverHideAt になった瞬間に
                // 閉じた口を消す。それまでは口の中が閉じた口の下で唇の線の幅のまま開き始め、
                // 消えた時点では線と同じくらいの細い口が見える(潰しながら消すと、口角から口の中がはみ出すため)。
                // 機能ノードは口の形(横幅)で使うので、視差ノードの縮尺で消す
                var closedDef = rig.Role("mouth_closed");
                var closedP = closedDef != null && closedDef.hide_when_open ? rig.P(closedDef) : null;
                Add(UnsignedTree(b, "Mouth Open", Use("FR_MouthOpenY"), v =>
                {
                    var clip = b.Clip($"Mouth Open {v}");
                    var sc = f.localScale;
                    var w0 = closedP != null && openDef.open_from_width > 0f ? openDef.open_from_width : 1f;
                    var wx = Mathf.Lerp(w0, 1f, Mathf.InverseLerp(MouthCoverHideAt, MouthCoverFullWidthAt, v));
                    b.Scale(clip, f, new Vector3(sc.x * wx, sc.y * Mathf.Max(v, 0.001f), sc.z));  // 0 だと行列が退化するため
                    if (closedP != null)
                    {
                        var cs = closedP.localScale;
                        b.Scale(clip, closedP, new Vector3(cs.x, v >= MouthCoverHideAt ? cs.y * 0.001f : cs.y, cs.z));
                    }
                    var u = MouthPictureOpen(hold, v);
                    if (exprMouth) b.MaterialFloat(clip, rig.Mesh, Expression.MouthOpenProp, u);
                    foreach (var set in mouthSets) MouthMorph(b, clip, rig, set, u);
                    return clip;
                }, (closedP != null
                    ? new[] { MouthCoverHideAt - 0.001f, MouthCoverHideAt, MouthCoverFullWidthAt }
                    : new float[0]).Concat(morphPoints).ToArray()));
                // Idle / Disabled では口を閉じる(生成直後の姿勢は開いた口が見えている)
                b.SetNeutral(f, "m_LocalScale", new Vector3(f.localScale.x, f.localScale.y * 0.001f, f.localScale.z));
            }
            else if (exprMouth)
            {
                Add(UnsignedTree(b, "Mouth Open", Use("FR_MouthOpenY"), v =>
                {
                    var clip = b.Clip($"Mouth Open {v}");
                    var u = MouthPictureOpen(hold, v);
                    b.MaterialFloat(clip, rig.Mesh, Expression.MouthOpenProp, u);
                    foreach (var set in mouthSets) MouthMorph(b, clip, rig, set, u);
                    return clip;
                }, morphPoints));
            }

            // 口の形(横幅): 口の部品(通常・表情とも)。伸び縮みで開く開いた口は除く(開閉のツリーが縮尺を書くため)
            var closeds = rig.Doc.layers.Where(l => Expression.IsMouth(l) && l != openDef).Select(l => rig.F(l)).ToArray();
            if (closeds.Length > 0)
            {
                Add(AxisTree(b, "Mouth Form", Use("FR_MouthForm"), null, (v, _) =>
                {
                    var clip = b.Clip($"Mouth Form {v:+0;-0;0}");
                    foreach (var f in closeds)
                    {
                        var sc = f.localScale;
                        b.Scale(clip, f, new Vector3(sc.x * (1f + s.MouthFormScale * v), sc.y, sc.z));
                    }
                    return clip;
                }));
            }

            foreach (var p in doc.@params.Where(p => !used.Contains(p.avatar)))
            {
                warnings.Add($"{p.avatar}: 対応する動き、またはレイヤーが無いため平滑化のみ生成しました");
            }

            var idle = BuildIdleClip(b, rig, s);
            var disabled = b.NeutralClip("Disabled (neutral pose)");

            var sm = b.AddLayer(MainLayer);
            var stDisabled = AddState(sm, "Disabled", disabled, new Vector3(300, 0, 0));
            var stIdle = AddState(sm, "Idle", idle, new Vector3(300, 120, 0));
            var stTracking = AddState(sm, "Tracking", tracking, new Vector3(300, 240, 0));
            sm.defaultState = stIdle;

            const string active = ParamsFile.ActiveFlag;
            const string enable = ParamsFile.EnableFlag;
            Transition(stDisabled, stTracking, s, (AnimatorConditionMode.If, enable), (AnimatorConditionMode.If, active));
            Transition(stDisabled, stIdle, s, (AnimatorConditionMode.If, enable), (AnimatorConditionMode.IfNot, active));
            Transition(stIdle, stTracking, s, (AnimatorConditionMode.If, active));
            Transition(stIdle, stDisabled, s, (AnimatorConditionMode.IfNot, enable));
            Transition(stTracking, stIdle, s, (AnimatorConditionMode.IfNot, active));
            Transition(stTracking, stDisabled, s, (AnimatorConditionMode.IfNot, enable));
        }

        // --- FR_AutoBlink(自動まばたき) -------------------------------------------------------------

        /// <summary>
        /// params.json に FR_AutoBlink(bool)があれば、Off / On の層を作る。On(FR_AutoBlink が ON)は AutoBlinkLoopSec 秒のループで、
        /// AutoBlinkTimes の各時刻に開き具合 FR_AutoBlinkOpen を 1 → 0 → 1 にする(Idle のまばたきと同じ速さ)。
        /// 両状態とも FR_AutoBlinkW(On 1 / Off 0)を書き、遷移のクロスフェードで滑らかに切り替わる。
        /// Tracking の目の開閉のツリーは FR_AutoBlinkW でトラッキングの開き具合と FR_AutoBlinkOpen を混ぜる(EyeOpenTree)。
        /// Idle / Disabled は読まない(Idle は今まで通り 4 秒ごとのまばたき)。作ったら true。
        /// </summary>
        static bool BuildAutoBlinkLayer(Builder b, AnimatorController controller, Rig rig, ParamsDoc doc, AnimatorSettings s,
            List<string> warnings)
        {
            if (!doc.flags.Any(f => f.avatar == ParamsFile.AutoBlinkFlag && !f.IsInt)) return false;
            if (rig.Role("eye_l_white") == null && rig.Role("eye_r_white") == null)
            {
                warnings.Add($"{ParamsFile.AutoBlinkFlag}: 白目のレイヤーが無いため自動まばたきを付けません");
                return false;
            }
            controller.AddParameter(new AnimatorControllerParameter
                { name = AutoBlinkOpenParam, type = AnimatorControllerParameterType.Float, defaultFloat = 1f });
            controller.AddParameter(new AnimatorControllerParameter
                { name = AutoBlinkWeightParam, type = AnimatorControllerParameterType.Float, defaultFloat = 0f });

            var off = b.Clip("AutoBlink Off");
            b.Param(off, AutoBlinkOpenParam, 1f);
            b.Param(off, AutoBlinkWeightParam, 0f);

            var on = b.Clip("AutoBlink On");
            var T = Mathf.Max(1f, s.AutoBlinkLoopSec);
            var keys = new List<Keyframe> { new Keyframe(0f, 1f) };
            var last = 0f;
            foreach (var t in (s.AutoBlinkTimes ?? new float[0]).OrderBy(x => x))
            {
                // 前のまばたきと重なるもの・ループの終わりに収まらないものは使わない
                if (t <= last || t + BlinkCloseSec + BlinkOpenSec >= T) continue;
                keys.Add(new Keyframe(t, 1f));
                keys.Add(new Keyframe(t + BlinkCloseSec, 0f));
                keys.Add(new Keyframe(t + BlinkCloseSec + BlinkOpenSec, 1f));
                last = t + BlinkCloseSec + BlinkOpenSec;
            }
            keys.Add(new Keyframe(T, 1f));
            AnimationUtility.SetEditorCurve(on, EditorCurveBinding.FloatCurve("", typeof(Animator), AutoBlinkOpenParam),
                new AnimationCurve(keys.ToArray()));
            AnimationUtility.SetEditorCurve(on, EditorCurveBinding.FloatCurve("", typeof(Animator), AutoBlinkWeightParam),
                new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(T, 1f)));
            var settings = AnimationUtility.GetAnimationClipSettings(on);
            settings.loopTime = true;
            AnimationUtility.SetAnimationClipSettings(on, settings);

            var sm = b.AddLayer(AutoBlinkLayer);
            var stOff = AddState(sm, "Off", off, new Vector3(300, 0, 0));
            var stOn = AddState(sm, "On", on, new Vector3(300, 120, 0));
            sm.defaultState = stOff;
            Transition(stOff, stOn, s, (AnimatorConditionMode.If, ParamsFile.AutoBlinkFlag));
            Transition(stOn, stOff, s, (AnimatorConditionMode.IfNot, ParamsFile.AutoBlinkFlag));
            return true;
        }

        /// <summary>
        /// 目の開閉のツリー。autoBlink なら、トラッキングの開き具合のツリーと FR_AutoBlinkOpen のツリー(同じクリップ)を
        /// FR_AutoBlinkW で混ぜる(ON の間はトラッキングの開閉を使わない)。トラッキングのパラメータが無ければ開いた目を使う。
        /// </summary>
        static Motion EyeOpenTree(Builder b, string name, string tracking, bool autoBlink, Func<float, AnimationClip> clipAt,
            float[] extra = null)
        {
            var clips = new Dictionary<float, AnimationClip>();
            AnimationClip At(float v)
            {
                if (!clips.TryGetValue(v, out var clip)) clips[v] = clip = clipAt(v);
                return clip;
            }
            var track = UnsignedTree(b, name, tracking, At, extra);
            if (!autoBlink) return track;
            var blink = UnsignedTree(b, $"{name} (auto blink)", AutoBlinkOpenParam, At, extra);
            var tree = b.Tree($"{name} / auto blink", BlendTreeType.Simple1D, AutoBlinkWeightParam);
            tree.AddChild(track ?? At(1f), 0f);
            tree.AddChild(blink, 1f);
            return tree;
        }

        // --- FR_Expression(表情) -----------------------------------------------------------------

        /// <summary>
        /// 表情があれば、Normal と表情ごとの状態(Expr1〜)のレイヤーを作る。各状態はマテリアルの _FR_ExprA〜D(表情の重み)だけを書く。
        /// 遷移はすべて Any State から(条件の組は互いに重ならないので、行き来を繰り返さない)。表情 k になる条件:
        ///   FR_Expr = k(メニュー。FR_Enable が ON のとき)
        ///   FR_Expr = 0 で、左手が表情 k のジェスチャー。または右手がそうで、左手がどの表情のジェスチャーでもない(左手が優先)
        ///   FR_Expr = 0 で、どちらの手も表情のジェスチャーでなく、k が自動の表情で、FR_Active かつ FR_MouthForm_s > しきい値
        /// どれでもなくなると Normal(自動の表情は、しきい値より AutoExprHysteresis 下がるか FR_Active が OFF で戻る)。
        /// FR_Expr が表情の数より大きいときも Normal。
        /// </summary>
        static void BuildExpressionLayer(Builder b, AnimatorController controller, Rig rig, ParamsDoc doc, AnimatorSettings s,
            List<string> warnings)
        {
            var names = Expression.Names(rig.Doc).Take(Expression.MaxExpressions).ToArray();
            if (names.Length == 0) return;
            if (!rig.HasMesh)
            {
                warnings.Add("表情がありますが、メッシュが見つからないため表情を付けません。「アバターを生成」をやり直してください");
                return;
            }

            var hasExpr = doc.flags.Any(f => f.avatar == ParamsFile.ExprFlag && f.IsInt);
            if (!hasExpr) warnings.Add($"params.json に {ParamsFile.ExprFlag}(int)が無いため、メニューで表情を選べません");
            var byGesture = new Dictionary<int, int>();  // ジェスチャー → 表情の番号
            for (var k = 1; k <= names.Length; k++)
            {
                if (s.ExprGestures == null || !s.ExprGestures.TryGetValue(names[k - 1], out var g) || g < 1 || g > 7) continue;
                if (byGesture.TryGetValue(g, out var other))
                {
                    warnings.Add($"表情「{names[k - 1]}」: ジェスチャーが表情「{names[other - 1]}」と同じなので使いません");
                    continue;
                }
                byGesture[g] = k;
            }
            var form = doc.@params.FirstOrDefault(p => p.avatar == "FR_MouthForm");
            var autoK = string.IsNullOrEmpty(s.AutoExpr) ? 0 : Array.IndexOf(names, s.AutoExpr) + 1;
            if (autoK > 0 && form == null)
            {
                warnings.Add("自動の表情: params.json に FR_MouthForm が無いため使いません");
                autoK = 0;
            }
            if (!hasExpr && byGesture.Count == 0 && autoK == 0)
            {
                warnings.Add("表情がありますが、切り替える方法(メニュー・ジェスチャー・自動)が無いため表情を付けません");
                return;
            }
            if (byGesture.Count > 0)
            {
                foreach (var name in new[] { GestureLeftParam, GestureRightParam })
                {
                    if (controller.parameters.All(p => p.name != name)) controller.AddParameter(name, AnimatorControllerParameterType.Int);
                }
            }

            var sm = b.AddLayer(ExpressionLayer);
            var props = Expression.WeightPropCount(names.Length);
            var states = new AnimatorState[names.Length + 1];
            for (var k = 0; k <= names.Length; k++)
            {
                var clip = b.Clip(k == 0 ? "Expression Normal" : $"Expression {k} {names[k - 1]}");
                for (var i = 0; i < props; i++) b.MaterialVector(clip, rig.Mesh, Expression.WeightProps[i], Expression.Weights(k, i));
                states[k] = AddState(sm, k == 0 ? "Normal" : $"Expr{k}", clip, new Vector3(500, k * 60, 0));
            }
            sm.defaultState = states[0];

            var enable = (AnimatorConditionMode.If, 0f, ParamsFile.EnableFlag);
            var menuOff = hasExpr
                ? new[] { (AnimatorConditionMode.Equals, 0f, ParamsFile.ExprFlag) }
                : new (AnimatorConditionMode, float, string)[0];
            (AnimatorConditionMode, float, string)[] NotMapped(string hand) =>
                byGesture.Keys.Select(g => (AnimatorConditionMode.NotEqual, (float)g, hand)).ToArray();

            for (var k = 1; k <= names.Length && hasExpr; k++)
                FadeFromAny(sm, states[k], s, enable, (AnimatorConditionMode.Equals, (float)k, ParamsFile.ExprFlag));
            foreach (var pair in byGesture)
            {
                var g = (float)pair.Key;
                FadeFromAny(sm, states[pair.Value], s, menuOff.Concat(new[] { enable, (AnimatorConditionMode.Equals, g, GestureLeftParam) }).ToArray());
                FadeFromAny(sm, states[pair.Value], s, menuOff.Concat(new[] { enable, (AnimatorConditionMode.Equals, g, GestureRightParam) })
                    .Concat(NotMapped(GestureLeftParam)).ToArray());
            }
            var released = menuOff.Concat(new[] { enable }).Concat(NotMapped(GestureLeftParam)).Concat(NotMapped(GestureRightParam)).ToArray();
            if (autoK > 0)
            {
                FadeFromAny(sm, states[autoK], s, released.Concat(new[]
                {
                    (AnimatorConditionMode.If, 0f, ParamsFile.ActiveFlag),
                    (AnimatorConditionMode.Greater, s.AutoExprThreshold, form.Smoothed),
                }).ToArray());
            }

            // Normal(上の条件がどれも成り立たない)
            FadeFromAny(sm, states[0], s, (AnimatorConditionMode.IfNot, 0f, ParamsFile.EnableFlag));
            if (hasExpr) FadeFromAny(sm, states[0], s, enable, (AnimatorConditionMode.Greater, (float)names.Length, ParamsFile.ExprFlag));
            if (autoK == 0)
            {
                FadeFromAny(sm, states[0], s, released);
            }
            else
            {
                FadeFromAny(sm, states[0], s, released.Concat(new[]
                    { (AnimatorConditionMode.Less, s.AutoExprThreshold - AutoExprHysteresis, form.Smoothed) }).ToArray());
                FadeFromAny(sm, states[0], s, released.Concat(new[] { (AnimatorConditionMode.IfNot, 0f, ParamsFile.ActiveFlag) }).ToArray());
            }
        }

        static void FadeFromAny(AnimatorStateMachine sm, AnimatorState to, AnimatorSettings s,
            params (AnimatorConditionMode mode, float threshold, string param)[] conditions)
        {
            var t = sm.AddAnyStateTransition(to);
            t.canTransitionToSelf = false;
            t.hasExitTime = false;
            t.hasFixedDuration = true;
            t.duration = s.ExprFadeSec;
            foreach (var (mode, threshold, param) in conditions) t.AddCondition(mode, threshold, param);
        }

        /// <summary>FR_Active=false の間のループ: まばたき(両目)と呼吸(体ルートの微小な上下)。</summary>
        static AnimationClip BuildIdleClip(Builder b, Rig rig, AnimatorSettings s)
        {
            var clip = b.NeutralClip("Idle");
            var T = s.IdleLoopSec;
            foreach (var side in new[] { "l", "r" })
            {
                var white = rig.Role($"eye_{side}_white");
                if (white == null) continue;
                if (rig.Lid(side) != null)
                {
                    // 開き具合の時系列: 0.06 秒で閉じ、0.08 秒で開く。動きの折れ目(EyeLidBreaks)にもキーを置く
                    var t0 = T * 0.5f;
                    var steps = EyeLidBreaks(rig, side, s).Concat(new[] { 0f, 1f }).Distinct().OrderByDescending(v => v).ToArray();
                    var keys = new List<(float time, float v)> { (0f, 1f) };
                    keys.AddRange(steps.Select(v => (t0 + BlinkCloseSec * (1f - v), v)));             // 閉じる(1 → 0)
                    keys.AddRange(steps.Reverse().Skip(1).Select(v => (t0 + BlinkCloseSec + BlinkOpenSec * v, v)));  // 開く(0 → 1)
                    keys.Add((T, 1f));
                    SetEyeLidCurves(b, clip, rig, side, s, keys.ToArray());
                    if (rig.Role($"eye_{side}_closed") != null)
                        b.MaterialCurve(clip, rig.Mesh, Expression.EyeOpenProp(side),
                            new AnimationCurve(keys.Select(k => new Keyframe(k.time, k.v)).ToArray()), 1f);
                    continue;
                }
                var f = rig.F(white);
                var sy = f.localScale.y;
                b.Curve(clip, f, "m_LocalScale", f.localScale, 1, new AnimationCurve(
                    new Keyframe(0f, sy), new Keyframe(T * 0.5f, sy), new Keyframe(T * 0.5f + BlinkCloseSec, sy * s.EyeClosedScale),
                    new Keyframe(T * 0.5f + BlinkCloseSec + BlinkOpenSec, sy), new Keyframe(T, sy)));
            }
            var py = rig.Body.localPosition.y;
            b.Curve(clip, rig.Body, "m_LocalPosition", rig.Body.localPosition, 1, new AnimationCurve(
                new Keyframe(0f, py), new Keyframe(T * 0.5f, py + s.BreathAmplitude), new Keyframe(T, py)));

            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = true;
            AnimationUtility.SetAnimationClipSettings(clip, settings);
            return clip;
        }

        // --- まぶた(EyeLid) -----------------------------------------------------------------------

        /// <summary>
        /// 開き具合の 0〜1 の間で動きが折れる点(ブレンドツリーとアイドルのキーに足す): 切り取り線がまつ毛に追いつく点と、
        /// 瞳のハイライトが押され始める点・止まる点。
        /// </summary>
        static float[] EyeLidBreaks(Rig rig, string side, AnimatorSettings s)
        {
            var list = new List<float> { EyeLid.CatchUpAt };
            var e = rig.Lid(side);
            var hi = HighlightRange(rig, side, s);
            if (e != null && hi != null && e.has_lash && e.travel_px > 0f)
            {
                var start = hi.Value.top - e.lash_anchor_y_px;  // まつ毛の下がり量がこれを超えると押し始める
                foreach (var d in new[] { start, start + hi.Value.maxPush })
                {
                    var v = 1f - d / e.travel_px;
                    if (v > 0.001f && v < 0.999f) list.Add(Mathf.Round(v * 1000f) / 1000f);
                }
            }
            return list.Distinct().ToArray();
        }

        /// <summary>瞳のハイライトの上端(キャンバス y)と、押されて下がれる最大量(px)。ハイライトが無ければ null。</summary>
        static (float top, float maxPush)? HighlightRange(Rig rig, string side, AnimatorSettings s)
        {
            var hi = rig.Role($"eye_{side}_highlight");
            if (hi == null) return null;
            var pupil = rig.Role($"eye_{side}_pupil");
            var white = rig.Role($"eye_{side}_white");
            var bottom = pupil?.bbox[3] ?? white?.bbox[3] ?? hi.bbox[3];
            return (hi.bbox[1], Mathf.Max(0f, bottom - hi.bbox[3]) * Mathf.Clamp01(s.EyeHighlightFollow));
        }

        /// <summary>開き具合 v での瞳のハイライトの下がり量(px)。</summary>
        static float HighlightPush(Rig rig, string side, AnimatorSettings s, float v)
        {
            var hi = HighlightRange(rig, side, s);
            var white = rig.Role($"eye_{side}_white");
            if (hi == null || white == null) return 0f;
            return EyeLid.HighlightPush(rig.Lid(side), hi.Value.top, hi.Value.maxPush, white.bbox[1], v);
        }

        /// <summary>開き具合 v(0=閉じ, 1=開き)でのまつ毛の位置・縮尺、切り取りのずらし量、まつ毛の下がり量(px)。</summary>
        static (Vector3 lashPos, Vector3 lashScale, float shift, float drop) EyeLidPose(Rig rig, string side, AnimatorSettings s, float v)
        {
            var e = rig.Lid(side);
            var shift = EyeLid.ClipShift(e, v);
            var drop = EyeLid.LashDrop(e, v);
            var lash = rig.Role($"eye_{side}_lash");
            if (lash == null || !e.has_lash) return (Vector3.zero, Vector3.one, shift, drop);
            var f = rig.F(lash);
            var m = rig.Info.meters_per_px;
            var k = EyeLid.LashScale(s.EyeLashClosedScale, v);
            // 縮尺は機能ノード(= レイヤーの pivot)を中心にかかるので、基準(まつ毛の下端)が動かないように補正する。
            // キャンバスの y は下向き、ローカルの y は上向き
            var down = EyeLid.LashDrop(e, v) + (e.lash_anchor_y_px - lash.pivot[1]) * (1f - k);
            var sc = f.localScale;
            return (f.localPosition + new Vector3(0f, -down * m, 0f), new Vector3(sc.x, sc.y * k, sc.z), shift, drop);
        }

        static void SetEyeLid(Builder b, AnimationClip clip, Rig rig, string side, AnimatorSettings s, float v)
        {
            var (pos, scale, shift, drop) = EyeLidPose(rig, side, s, v);
            var lash = rig.Role($"eye_{side}_lash");
            if (lash != null && rig.Lid(side).has_lash)
            {
                var f = rig.F(lash);
                b.Pos(clip, f, pos);
                b.Scale(clip, f, scale);
            }
            b.MaterialFloat(clip, rig.Mesh, EyeLid.LidProp(side), shift);
            // 白目の影: まつ毛と同じだけ下げる(シェーダは下がった分を戻した位置の白目で切り取る)
            var shadow = rig.Role($"eye_{side}_shadow");
            if (shadow != null)
            {
                var f = rig.F(shadow);
                b.Pos(clip, f, f.localPosition + new Vector3(0f, -drop * rig.Info.meters_per_px, 0f));
                b.MaterialFloat(clip, rig.Mesh, EyeLid.LashProp(side), drop);
            }
            // 瞳のハイライト: 黒目の子として目線に付いて動き、まぶたの線が届くと押し下げられる(下がった分はシェーダが戻して切り取る)
            var highlight = rig.Role($"eye_{side}_highlight");
            if (highlight != null)
            {
                var push = HighlightPush(rig, side, s, v);
                var f = rig.F(highlight);
                b.Pos(clip, f, f.localPosition + new Vector3(0f, -push * rig.Info.meters_per_px, 0f));
                b.MaterialFloat(clip, rig.Mesh, EyeLid.HighlightProp(side), push);
            }
        }

        /// <summary>(時刻, 開き具合) の列からまぶたのカーブを作る(Idle 用)。</summary>
        static void SetEyeLidCurves(Builder b, AnimationClip clip, Rig rig, string side, AnimatorSettings s,
            (float time, float v)[] keys)
        {
            var poses = keys.Select(k => (k.time, pose: EyeLidPose(rig, side, s, k.v))).ToArray();
            AnimationCurve Curve(Func<(Vector3 lashPos, Vector3 lashScale, float shift, float drop), float> value) =>
                new AnimationCurve(poses.Select(p => new Keyframe(p.time, value(p.pose))).ToArray());
            var lash = rig.Role($"eye_{side}_lash");
            if (lash != null && rig.Lid(side).has_lash)
            {
                var f = rig.F(lash);
                b.Curve(clip, f, "m_LocalPosition", f.localPosition, 1, Curve(p => p.lashPos.y));
                b.Curve(clip, f, "m_LocalScale", f.localScale, 1, Curve(p => p.lashScale.y));
            }
            b.MaterialCurve(clip, rig.Mesh, EyeLid.LidProp(side), Curve(p => p.shift));
            var shadow = rig.Role($"eye_{side}_shadow");
            if (shadow != null)
            {
                var f = rig.F(shadow);
                var y = f.localPosition.y;
                var m = rig.Info.meters_per_px;
                b.Curve(clip, f, "m_LocalPosition", f.localPosition, 1, Curve(p => y - p.drop * m));
                b.MaterialCurve(clip, rig.Mesh, EyeLid.LashProp(side), Curve(p => p.drop));
            }
            var highlight = rig.Role($"eye_{side}_highlight");
            if (highlight != null)
            {
                var f = rig.F(highlight);
                var y = f.localPosition.y;
                var m = rig.Info.meters_per_px;
                var pushes = keys.Select(k => (k.time, push: HighlightPush(rig, side, s, k.v))).ToArray();
                b.Curve(clip, f, "m_LocalPosition", f.localPosition, 1,
                    new AnimationCurve(pushes.Select(p => new Keyframe(p.time, y - p.push * m)).ToArray()));
                b.MaterialCurve(clip, rig.Mesh, EyeLid.HighlightProp(side),
                    new AnimationCurve(pushes.Select(p => new Keyframe(p.time, p.push)).ToArray()));
            }
        }

        // --- ブレンドツリーの部品 -----------------------------------------------------------------

        /// <summary>signed の 1 軸(−1/0/+1)または 2 軸(3×3)のツリー。px が無ければ null。</summary>
        static Motion AxisTree(Builder b, string name, string px, string py, Func<int, int, AnimationClip> clipAt)
        {
            if (px == null && py == null) return null;
            if (px == null || py == null)
            {
                var param = px ?? py;
                var tree1 = b.Tree(name, BlendTreeType.Simple1D, param);
                foreach (var v in new[] { -1, 0, 1 }) tree1.AddChild(px != null ? clipAt(v, 0) : clipAt(0, v), v);
                return tree1;
            }

            var tree = b.Tree(name, BlendTreeType.FreeformCartesian2D, px, py);
            foreach (var y in new[] { -1, 0, 1 })
            {
                foreach (var x in new[] { -1, 0, 1 }) tree.AddChild(clipAt(x, y), new Vector2(x, y));
            }
            return tree;
        }

        static float MouthHold(AnimatorSettings s) => Mathf.Round(Mathf.Clamp(s.MouthClosedHold, 0f, 0.4f) * 1000f) / 1000f;

        /// <summary>
        /// 口の絵を選ぶ開き具合(_FR_MouthOpen)。トラッキングの開き具合 v が hold 以下なら 0(最初の絵 = 閉じた口のまま)、
        /// hold〜0.5 を 0〜0.5 に伸ばし、0.5 以上はそのまま(半開きの絵は 50%、開いた口は 100% のまま)。
        /// ツリーの点に hold と 0.5 を置くので、その間はこの折れ線どおりに動く。
        /// </summary>
        static float MouthPictureOpen(float hold, float v) =>
            v <= hold ? 0f : v < 0.5f ? (v - hold) / (0.5f - hold) * 0.5f : v;

        /// <summary>口の絵の組の、それぞれの絵になる開き具合(ブレンドツリーの点。この間は直線で動く)。</summary>
        static float[] MorphPoints(List<LayerDef[]> sets) => sets
            .SelectMany(set => Expression.Keys(set.Length))
            .Select(v => Mathf.Round(v * 1000f) / 1000f).Distinct().ToArray();

        /// <summary>
        /// 開き具合 v での、口の絵の組 set(閉じ → 開き)の大きさ合わせ。隣り合う絵の基準(Keys)の間では、
        /// 下の絵は上の絵の大きさ・中心へ、上の絵は下の絵の大きさ・中心から自分へ、t(0〜1)に比例して近づける
        /// (混ぜる割合はシェーダが同じ間で決める)。大きさは視差ノードの縮尺、位置のずれは機能ノードの位置で付ける
        /// (機能ノードの縮尺は口の形が、視差ノードの位置は頭の視差が書くため)。基準ちょうどでは元の大きさ・位置。
        /// </summary>
        static void MouthMorph(Builder b, AnimationClip clip, Rig rig, LayerDef[] set, float v)
        {
            var keys = Expression.Keys(set.Length);
            var m = rig.Info.meters_per_px;
            for (var i = 0; i < set.Length; i++)
            {
                LayerDef other = null;
                var t = 0f;
                if (i + 1 < set.Length && v > keys[i] && v <= keys[i + 1] + 1e-4f)
                {
                    other = set[i + 1];
                    t = Mathf.InverseLerp(keys[i], keys[i + 1], v);
                }
                else if (i > 0 && v < keys[i] && v >= keys[i - 1] - 1e-4f)
                {
                    other = set[i - 1];
                    t = 1f - Mathf.InverseLerp(keys[i - 1], keys[i], v);
                }
                var self = set[i];
                var p = rig.P(self);
                var f = rig.F(self);
                if (other == null || t <= 0f)
                {
                    b.Scale(clip, p, p.localScale);
                    b.Pos(clip, f, f.localPosition);
                    continue;
                }
                var sx = Mathf.Lerp(1f, other.Width / self.Width, t);
                var sy = Mathf.Lerp(1f, other.Height / self.Height, t);
                // キャンバス px。視差ノード(= pivot)を中心に縮尺すると中心が動くので、相手の中心へ寄せる分との差を機能ノードで足す
                float Cx(LayerDef l) => (l.bbox[0] + l.bbox[2]) * 0.5f;
                float Cy(LayerDef l) => (l.bbox[1] + l.bbox[3]) * 0.5f;
                var dx = Mathf.Lerp(Cx(self), Cx(other), t) - (self.pivot[0] + sx * (Cx(self) - self.pivot[0]));
                var dy = Mathf.Lerp(Cy(self), Cy(other), t) - (self.pivot[1] + sy * (Cy(self) - self.pivot[1]));
                var ps = p.localScale;
                b.Scale(clip, p, new Vector3(ps.x * sx, ps.y * sy, ps.z));
                // キャンバスの右 = ローカルの −X、下 = −Y。機能ノードの位置は縮尺した視差ノードの中なので縮尺で割る
                b.Pos(clip, f, f.localPosition + new Vector3(-dx * m / (ps.x * sx), -dy * m / (ps.y * sy), 0f));
            }
        }

        /// <summary>口の隠しの構造で、閉じた口が消える開き具合(この直前までは閉じた口をそのまま表示)。</summary>
        const float MouthCoverHideAt = 0.1f;

        /// <summary>口の隠しの構造で、口の中が唇の線の幅から本来の幅に広がりきる開き具合。</summary>
        const float MouthCoverFullWidthAt = 0.5f;

        /// <summary>unsigned(0/1)の 1 軸ツリー。extra で 0 と 1 の間にも点を置ける。</summary>
        static Motion UnsignedTree(Builder b, string name, string param, Func<float, AnimationClip> clipAt,
            float[] extra = null)
        {
            if (param == null) return null;
            var tree = b.Tree(name, BlendTreeType.Simple1D, param);
            foreach (var v in new[] { 0f }.Concat(extra ?? new float[0]).Concat(new[] { 1f }).Distinct().OrderBy(x => x))
                tree.AddChild(clipAt(v), v);
            return tree;
        }

        static void AddDirectChild(BlendTree direct, Motion motion)
        {
            direct.AddChild(motion);
            var children = direct.children;
            children[children.Length - 1].directBlendParameter = OneParam;
            direct.children = children;
        }

        static AnimatorState AddState(AnimatorStateMachine sm, string name, Motion motion, Vector3 position)
        {
            var state = sm.AddState(name, position);
            state.motion = motion;
            state.writeDefaultValues = true;
            return state;
        }

        static void Transition(AnimatorState from, AnimatorState to, AnimatorSettings s,
            params (AnimatorConditionMode mode, string param)[] conditions)
        {
            var t = from.AddTransition(to);
            t.hasExitTime = false;
            t.hasFixedDuration = true;
            t.duration = s.TransitionSec;
            foreach (var (mode, param) in conditions) t.AddCondition(mode, 0f, param);
        }

        /// <summary>Scene 上のリグ(RigBuilder の命名規則)と layers.json の対応。</summary>
        sealed class Rig
        {
            public readonly LayersDoc Doc;
            public readonly RigInfo Info;
            public readonly Transform Head;
            public readonly Transform Body;
            public readonly Transform Mesh;  // SkinnedMeshRenderer(まぶたのマテリアル値を動かす)
            readonly Dictionary<string, Transform> byName = new Dictionary<string, Transform>();

            public Rig(GameObject root, LayersDoc doc, RigInfo info)
            {
                Doc = doc;
                Info = info;
                foreach (var t in root.GetComponentsInChildren<Transform>(true)) byName[t.name] = t;
                byName.TryGetValue(RigBuilder.HeadRootName, out Head);
                byName.TryGetValue(RigBuilder.BodyRootName, out Body);
                byName.TryGetValue(RigBuilder.MeshObjectName, out Mesh);
            }

            /// <summary>片目のまばたきの寸法。古い rig.json・白目が無い・メッシュが無いときは null(旧方式で閉じる)。</summary>
            public EyeLidInfo Lid(string side)
            {
                var e = side == "l" ? Info.eye_l : Info.eye_r;
                return e != null && e.valid && Mesh != null && Mesh.GetComponent<SkinnedMeshRenderer>() != null ? e : null;
            }

            public bool HasMesh => Mesh != null && Mesh.GetComponent<SkinnedMeshRenderer>() != null;

            public bool Validate(List<string> errors)
            {
                var missing = Doc.layers.Where(l => P(l) == null || F(l) == null).Select(l => l.name).ToList();
                if (Head == null) missing.Add(RigBuilder.HeadRootName);
                if (Body == null) missing.Add(RigBuilder.BodyRootName);
                if (missing.Count == 0) return true;
                errors.Add($"Scene のアバターが layers.json と一致しません(見つからない: {string.Join(", ", missing)})。「アバターを生成」をやり直してください");
                return false;
            }

            public LayerDef Role(string role) => Doc.layers.FirstOrDefault(l => l.role == role);
            public Transform P(LayerDef l) => byName.TryGetValue(l.name + RigBuilder.ParallaxSuffix, out var t) ? t : null;
            public Transform F(LayerDef l) => byName.TryGetValue(l.name + RigBuilder.FunctionSuffix, out var t) ? t : null;
            public Transform Front(LayerDef l) => byName.TryGetValue(l.name + RigBuilder.FrontSuffix, out var t) ? t : null;

            /// <summary>黒目・まつ毛は白目の子なので、白目との depth の差だけ動かす(瞳のハイライトは黒目との差)。</summary>
            public float ParallaxDepth(LayerDef l)
            {
                if (!RigBuilder.IsEyeAttached(l)) return l.depth;
                var pupil = RigBuilder.IsHighlight(l) ? Role(RigBuilder.EyePupilRole(l.role)) : null;
                if (pupil != null) return l.depth - pupil.depth;
                var white = Role(RigBuilder.EyeWhiteRole(l.role));
                return white != null ? l.depth - white.depth : l.depth;
            }
        }

        /// <summary>クリップ・ツリーをコントローラのサブアセットとして作り、既定値(Disabled 用)を集める。</summary>
        sealed class Builder
        {
            readonly AnimatorController controller;
            readonly Transform root;
            readonly Dictionary<string, (EditorCurveBinding binding, float value)> defaults =
                new Dictionary<string, (EditorCurveBinding, float)>();

            public Builder(AnimatorController controller, Transform root)
            {
                this.controller = controller;
                this.root = root;
            }

            public AnimationClip Clip(string name)
            {
                var clip = new AnimationClip { name = name };
                AssetDatabase.AddObjectToAsset(clip, controller);
                return clip;
            }

            public BlendTree Tree(string name, BlendTreeType type, string px, string py = null)
            {
                var tree = new BlendTree
                {
                    name = name,
                    blendType = type,
                    blendParameter = px,
                    blendParameterY = py ?? px,
                    useAutomaticThresholds = false,
                    hideFlags = HideFlags.HideInHierarchy,
                };
                AssetDatabase.AddObjectToAsset(tree, controller);
                return tree;
            }

            public AnimatorStateMachine AddLayer(string name)
            {
                var sm = new AnimatorStateMachine { name = name, hideFlags = HideFlags.HideInHierarchy };
                AssetDatabase.AddObjectToAsset(sm, controller);
                controller.AddLayer(new AnimatorControllerLayer { name = name, stateMachine = sm, defaultWeight = 1f });
                return sm;
            }

            public void Param(AnimationClip clip, string name, float value) =>
                AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve("", typeof(Animator), name), Const(value));

            /// <summary>コンポーネントの Vector3 の 3 成分を一定値で動かす(中立の値は記録しない)。</summary>
            public void ComponentVector(AnimationClip clip, Transform t, Type type, string prop, Vector3 v)
            {
                for (var i = 0; i < 3; i++) ComponentFloat(clip, t, type, $"{prop}.{"xyz"[i]}", v[i]);
            }

            /// <summary>コンポーネントの float(例: Constraint の GlobalWeight)を一定値で動かす。</summary>
            public void ComponentFloat(AnimationClip clip, Transform t, Type type, string prop, float value) =>
                AnimationUtility.SetEditorCurve(clip,
                    EditorCurveBinding.FloatCurve(AnimationUtility.CalculateTransformPath(t, root), type, prop), Const(value));

            public void Pos(AnimationClip clip, Transform t, Vector3 v) => Vec(clip, t, "m_LocalPosition", v, t.localPosition);
            public void Scale(AnimationClip clip, Transform t, Vector3 v) => Vec(clip, t, "m_LocalScale", v, t.localScale);
            public void Euler(AnimationClip clip, Transform t, Vector3 v) => Vec(clip, t, "localEulerAnglesRaw", v, t.localEulerAngles);

            /// <summary>ベクトルの 1 成分(axis)だけ curve で動かし、残りの成分は既定値で固定する。</summary>
            public void Curve(AnimationClip clip, Transform t, string prop, Vector3 def, int axis, AnimationCurve curve)
            {
                for (var i = 0; i < 3; i++)
                {
                    var binding = Binding(t, prop, i, def[i]);
                    AnimationUtility.SetEditorCurve(clip, binding, i == axis ? curve : Const(def[i]));
                }
            }

            /// <summary>SkinnedMeshRenderer のマテリアルの float を一定値で動かす(中立の値は neutral。既定 0)。</summary>
            public void MaterialFloat(AnimationClip clip, Transform renderer, string prop, float value, float neutral = 0f) =>
                AnimationUtility.SetEditorCurve(clip, MaterialBinding(renderer, prop, neutral), Const(value));

            public void MaterialCurve(AnimationClip clip, Transform renderer, string prop, AnimationCurve curve, float neutral = 0f) =>
                AnimationUtility.SetEditorCurve(clip, MaterialBinding(renderer, prop, neutral), curve);

            /// <summary>マテリアルの Vector の 4 成分を一定値で動かす(中立の値は 0)。</summary>
            public void MaterialVector(AnimationClip clip, Transform renderer, string prop, Vector4 value)
            {
                for (var i = 0; i < 4; i++) MaterialFloat(clip, renderer, $"{prop}.{"xyzw"[i]}", value[i]);
            }

            EditorCurveBinding MaterialBinding(Transform renderer, string prop, float neutral)
            {
                var path = AnimationUtility.CalculateTransformPath(renderer, root);
                var name = $"material.{prop}";
                var binding = EditorCurveBinding.FloatCurve(path, typeof(SkinnedMeshRenderer), name);
                var key = $"{path}|{name}";
                if (!defaults.ContainsKey(key)) defaults[key] = (binding, neutral);
                return binding;
            }

            /// <summary>Idle / Disabled での値を既定値から差し替える(例: 口を閉じる)。</summary>
            public void SetNeutral(Transform t, string prop, Vector3 v)
            {
                for (var i = 0; i < 3; i++) Binding(t, prop, i, v[i], overwrite: true);
            }

            /// <summary>これまでに動かしたすべての Transform プロパティを中立の値に置くクリップ。</summary>
            public AnimationClip NeutralClip(string name)
            {
                var clip = Clip(name);
                foreach (var (binding, value) in defaults.Values) AnimationUtility.SetEditorCurve(clip, binding, Const(value));
                return clip;
            }

            void Vec(AnimationClip clip, Transform t, string prop, Vector3 v, Vector3 def)
            {
                for (var i = 0; i < 3; i++) AnimationUtility.SetEditorCurve(clip, Binding(t, prop, i, def[i]), Const(v[i]));
            }

            /// <summary>binding を返し、初めて見るプロパティなら中立の値として def を記録する。</summary>
            EditorCurveBinding Binding(Transform t, string prop, int axis, float def, bool overwrite = false)
            {
                var path = AnimationUtility.CalculateTransformPath(t, root);
                var name = $"{prop}.{"xyz"[axis]}";
                var binding = EditorCurveBinding.FloatCurve(path, typeof(Transform), name);
                var key = $"{path}|{name}";
                if (overwrite || !defaults.ContainsKey(key)) defaults[key] = (binding, def);
                return binding;
            }

            static AnimationCurve Const(float value) =>
                new AnimationCurve(new Keyframe(0f, value), new Keyframe(1f / 60f, value));
        }
    }
}
