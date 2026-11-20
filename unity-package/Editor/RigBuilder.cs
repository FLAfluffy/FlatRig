using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace FlatRig.Editor
{
    /// <summary>生成の設定。長さの単位は m。</summary>
    public class RigSettings
    {
        public string RigName = "FlatRig";
        public float CanvasHeight = 0.8f;      // キャンバス全体の高さ
        public float BottomY = 0.8f;           // キャンバス下端の高さ(足元からの距離)
        public int Padding = 4;                // アトラス内のレイヤー間の余白(px)
        public int MaxAtlasSize = 4096;
        public float LayerSpacing = 0.0005f;   // order 1 つあたりの前後のずれ(手前 = +Z)
        public bool Limbs;                     // 手足をトラッキングに合わせる(LimbRig。Humanoid にする)
        public bool FreezeArms = true;         // 手足の設定が ON のとき、Expression Menu の「腕を固定」の初期値
        public bool FreezeLegs = true;         // 同「脚を固定」の初期値
        public bool HandCam = true;            // 同「手をカメラで動かす」の初期値(VTube Studio の手のトラッキング。§5.11)
        public string[] DrawOrder;             // 表示順(奥 → 手前。DrawOrder のキー)。空なら PSD の重なり順(§5.12)
    }

    /// <summary>生成時の寸法。AnimatorGenerator が動きの振幅(m)を求めるのに使う(rig.json)。</summary>
    [System.Serializable]
    public class RigInfo
    {
        public float meters_per_px;
        public float canvas_width_m;
        public float canvas_height_m;
        public EyeLidInfo eye_l = new EyeLidInfo();  // まばたきの寸法(EyeLid)。古い rig.json では valid=false
        public EyeLidInfo eye_r = new EyeLidInfo();
        public bool arms_frozen = true;        // 「腕を固定」「脚を固定」の初期値(RigSettings.FreezeArms / FreezeLegs)
        public bool legs_frozen = true;
        public bool hand_cam = true;           // 「手をカメラで動かす」の初期値(RigSettings.HandCam)
        public string[] draw_order = new string[0];  // 実際の表示順(奥 → 手前。DrawOrder のキー。記録用)
        public LimbInfo[] limbs = new LimbInfo[0];  // 手足の関節(手足の設定が ON のとき。手の追従の IK に使う)
    }

    /// <summary>手足 1 本の関節(rig.json)。joints はルート基準の x, y(m)を並べたもの。joints[i] は segments[i] の根元、最後は先端。</summary>
    [System.Serializable]
    public class LimbInfo
    {
        public string name;
        public string[] segments;
        public float[] joints;

        public Vector2 Joint(int i) => new Vector2(joints[i * 2], joints[i * 2 + 1]);
        public int JointCount => joints == null ? 0 : joints.Length / 2;
    }

    /// <summary>
    /// layers.json から平面アバターの階層・メッシュ・マテリアルを生成する(docs/SPEC.md §5.4, §5.6)。
    /// 階層:
    ///   {RigName}
    ///     FR_Mesh                      SkinnedMeshRenderer(1 メッシュ・1 マテリアル)
    ///     FR_Armature/FR_BodyRoot      体ルート(body_pivot)
    ///       {layer}_P/{layer}_F        group=body のレイヤー
    ///       FR_HeadRoot                頭ルート(head_pivot)
    ///         {layer}_P/{layer}_F      group=head のレイヤー(_P = 視差ノード、_F = 機能ノード)
    ///       {role}_J/…                 手足の関節ノード(手足の設定が ON のとき。LimbRig)。手足のレイヤーはこの下
    ///       FR_LimbShadow, FR_LimbAim_* 手足の向きを求める補助(同上)
    ///     FR_Humanoid/FR_Hips/…        見えない人型の骨格(同上)
    /// 黒目・まつ毛・白目の影(eye_*_pupil, eye_*_lash, eye_*_shadow)の _P は同じ側の白目の _F の子にする(目の位置の動きに追従するため)。
    /// 白目・黒目・白目の影の頂点には、まばたきの切り取り用の UV1〜3 を持たせる(EyeLid, FlatRig/Unlit)。
    /// すべての頂点の UV4 には、表情と絵の差し替えの表示の条件を入れる(Expression.Uv4)。
    /// アバターは +Z を向き、キャンバスの右は正面から見て右(= −X)になる。
    /// </summary>
    public static class RigBuilder
    {
        public const string GeneratedRoot = "Assets/FlatRig/Generated";
        public const string ShaderName = "FlatRig/Unlit";
        public const string MeshObjectName = "FR_Mesh";
        public const string ArmatureName = "FR_Armature";
        public const string BodyRootName = "FR_BodyRoot";
        public const string HeadRootName = "FR_HeadRoot";
        public const string ParallaxSuffix = "_P";
        public const string FunctionSuffix = "_F";
        public const string FrontSuffix = "_Front";  // 腕・手の手前用の複製(§5.10。動かしている間だけ表示)

        static readonly Regex SafeName = new Regex("^[A-Za-z0-9_-]{1,64}$");

        /// <summary>生成物のフォルダ(Assets/FlatRig/Generated/{RigName})。</summary>
        public static string OutputFolder(string rigName) => $"{GeneratedRoot}/{rigName}";

        /// <summary>
        /// 生成して Scene に置いた GameObject を返す。失敗時は null(理由は errors)。
        /// 同名の GameObject が Scene にあれば、confirmReplace が true のときは確認してから置き換える。
        /// </summary>
        public static GameObject Build(string layersJsonPath, RigSettings s, List<string> errors, List<string> warnings,
            bool confirmReplace = true)
        {
            if (!SafeName.IsMatch(s.RigName ?? ""))
            {
                errors.Add("アバター名は英数字と _ - の 64 文字以内にしてください");
                return null;
            }
            if (s.CanvasHeight <= 0f)
            {
                errors.Add("キャンバスの高さは正の値にしてください");
                return null;
            }
            var shader = Shader.Find(ShaderName);
            if (shader == null)
            {
                errors.Add($"シェーダ {ShaderName} が見つかりません(パッケージの Shaders フォルダを確認してください)");
                return null;
            }

            var loaded = LayersImporter.Load(layersJsonPath, errors);
            if (loaded == null) return null;

            Texture2D atlas = null;
            try
            {
                atlas = LayersImporter.BuildAtlas(loaded.Textures, s.Padding, s.MaxAtlasSize, out var rects, errors, warnings);
                if (atlas == null) return null;

                var existing = SceneManager.GetActiveScene().GetRootGameObjects().FirstOrDefault(g => g.name == s.RigName);
                if (existing != null)
                {
                    if (confirmReplace && !EditorUtility.DisplayDialog("FlatRig",
                            $"Scene の「{s.RigName}」を作り直します。よろしいですか?", "作り直す", "やめる"))
                    {
                        errors.Add("作り直しを中止しました");
                        return null;
                    }
                    Undo.DestroyObjectImmediate(existing);
                }

                var folder = OutputFolder(s.RigName);
                EnsureFolder(folder);
                File.WriteAllText(ToFullPath($"{folder}/layers.json"), loaded.JsonText);
                AssetDatabase.ImportAsset($"{folder}/layers.json");
                var k = s.CanvasHeight / loaded.Doc.canvas[1];
                var info = new RigInfo { meters_per_px = k, canvas_width_m = loaded.Doc.canvas[0] * k, canvas_height_m = s.CanvasHeight };
                info.eye_l = MeasureEye(loaded, "l");
                info.eye_r = MeasureEye(loaded, "r");
                info.arms_frozen = s.FreezeArms;
                info.legs_frozen = s.FreezeLegs;
                info.hand_cam = s.HandCam;

                var limbs = s.Limbs ? EstimateLimbs(loaded, warnings) : null;
                if (limbs != null) info.limbs = limbs.Select(l => LimbInfoOf(l, loaded.Doc, s)).ToArray();
                var jointRoles = new HashSet<string>(limbs?.SelectMany(l => l.Segments) ?? Enumerable.Empty<string>());
                var fronts = DrawOrder.FrontIndices(loaded.Ordered, l => jointRoles.Contains(l.role));
                info.draw_order = DrawOrder.Merge(DrawOrder.DefaultKeys(loaded.Ordered, fronts), s.DrawOrder).ToArray();
                File.WriteAllText(ToFullPath($"{folder}/rig.json"), JsonUtility.ToJson(info, true));
                AssetDatabase.ImportAsset($"{folder}/rig.json");
                var atlasAsset = SaveAtlas(atlas, $"{folder}/{s.RigName}_atlas.png");
                var material = SaveMaterial(shader, atlasAsset, $"{folder}/{s.RigName}_mat.mat");
                var root = BuildHierarchy(loaded, rects, s, material, $"{folder}/{s.RigName}_mesh.asset", limbs,
                    $"{folder}/{s.RigName}_Avatar.asset", errors, warnings);
                if (root == null) return null;

                AssetDatabase.SaveAssets();
                Undo.RegisterCreatedObjectUndo(root, "FlatRig Build");
                EditorSceneManager.MarkSceneDirty(root.scene);
                Selection.activeGameObject = root;
                return root;
            }
            finally
            {
                if (atlas != null) Object.DestroyImmediate(atlas);
                loaded.DestroyTextures();
            }
        }

        /// <summary>関節をルート基準の位置(BuildHierarchy の ToLocal と同じ式)にして rig.json 用にする。</summary>
        static LimbInfo LimbInfoOf(LimbRig.Limb limb, LayersDoc doc, RigSettings s)
        {
            float cw = doc.canvas[0], ch = doc.canvas[1];
            var k = s.CanvasHeight / ch;
            return new LimbInfo
            {
                name = limb.Name,
                segments = limb.Segments.ToArray(),
                joints = limb.Joints.SelectMany(p => new[] { -(p.x - cw * 0.5f) * k, s.BottomY + (ch - p.y) * k }).ToArray(),
            };
        }

        /// <summary>手足の関節を求める。手足が 1 本も使えなければ null(手足の設定が OFF と同じ)。</summary>
        static List<LimbRig.Limb> EstimateLimbs(LoadedLayers loaded, List<string> warnings)
        {
#if FLATRIG_VRCSDK
            var limbs = LimbRig.Estimate(loaded, warnings);
            if (limbs.Count > 0) return limbs;
            warnings.Add("「手足をトラッキングに合わせる」が ON ですが、手足の部品(arm_l_upper など)が無いため、これまでどおり Generic で作ります");
#else
            warnings.Add("VRChat SDK が無いため、「手足をトラッキングに合わせる」は使いません");
#endif
            return null;
        }

        static GameObject BuildHierarchy(LoadedLayers loaded, Rect[] rects, RigSettings s, Material material, string meshPath,
            List<LimbRig.Limb> limbs, string avatarPath, List<string> errors, List<string> warnings)
        {
            var doc = loaded.Doc;
            float cw = doc.canvas[0], ch = doc.canvas[1];
            var k = s.CanvasHeight / ch;
            Vector3 ToLocal(float px, float py) => new Vector3(-(px - cw * 0.5f) * k, s.BottomY + (ch - py) * k, 0f);

            var root = new GameObject(s.RigName);
            root.AddComponent<Animator>().applyRootMotion = false;
            var meshObject = NewChild(MeshObjectName, root.transform, Vector3.zero);
            var armature = NewChild(ArmatureName, root.transform, Vector3.zero);

            var bodyPivot = HasPivot(doc.body_pivot) ? doc.body_pivot : new[] { cw * 0.5f, ch };
            var headPivot = HasPivot(doc.head_pivot) ? doc.head_pivot : DefaultHeadPivot(doc, cw, ch);
            var bodyRoot = NewChild(BodyRootName, armature, ToLocal(bodyPivot[0], bodyPivot[1]));
            var headRoot = NewChild(HeadRootName, bodyRoot, ToLocal(headPivot[0], headPivot[1]));
            Vector3 ToLocalV(Vector2 p) => ToLocal(p.x, p.y);
            var jointNodes = limbs != null
                ? LimbRig.CreateJointNodes(limbs, bodyRoot, ToLocalV, NewChild)
                : new Dictionary<string, Transform>();

            // 白目を先に作り、黒目・まつ毛・白目の影をその機能ノードの下に、瞳のハイライトを黒目の機能ノードの下に入れる
            var functionNodes = new Dictionary<string, Transform>();
            foreach (var layer in loaded.Ordered.OrderBy(l => IsHighlight(l) ? 2 : IsEyeAttached(l) ? 1 : 0).ThenBy(l => l.order))
            {
                var parent = jointNodes.TryGetValue(layer.role, out var joint) ? joint
                    : layer.group == "head" ? headRoot : bodyRoot;
                if (IsEyeAttached(layer))
                {
                    var white = loaded.Ordered.FirstOrDefault(l => l.role == EyeWhiteRole(layer.role));
                    if (white != null) parent = functionNodes[white.name];
                    var pupil = IsHighlight(layer) ? loaded.Ordered.FirstOrDefault(l => l.role == EyePupilRole(layer.role)) : null;
                    if (pupil != null) parent = functionNodes[pupil.name];
                }
                var parallax = NewChild(layer.name + ParallaxSuffix, parent, ToLocal(layer.pivot[0], layer.pivot[1]));
                functionNodes[layer.name] = NewChild(layer.name + FunctionSuffix, parallax, parallax.position);
            }

            // 腕・手の部品は、一番手前にもう 1 枚ずつ描く(手前用の複製。ふだんは縮めて隠し、動かしている間だけ元と入れ替える)
            // 複製どうしは 上腕 → 前腕 → 手 の順に手前へ重ねる(曲げたときに前腕・手が上腕の後ろに隠れないように)
            var fronts = DrawOrder.FrontIndices(loaded.Ordered, l => jointNodes.ContainsKey(l.role));
            var frontNodes = fronts.Select(i => loaded.Ordered[i].name)
                .Select(name => NewChild(name + FrontSuffix, functionNodes[name].parent, functionNodes[name].parent.position)).ToArray();

            // 頂点は表示順(奥 → 手前)に並べ、三角形の並びで描画順を固定する。
            // 表示順は既定で order 順 → 手前用の複製。ウィンドウで並べ替えたらその順(§5.12)
            var n0 = loaded.Ordered.Length;
            var defaultKeys = DrawOrder.DefaultKeys(loaded.Ordered, fronts);
            var drawKeys = DrawOrder.Merge(defaultKeys, s.DrawOrder);
            var draw = drawKeys.Select(key => defaultKeys.IndexOf(key)).ToArray();  // 既定の順での位置(n0 以上は手前用の複製)
            var bones = draw.Select(d => d < n0 ? functionNodes[loaded.Ordered[d].name] : frontNodes[d - n0]).ToArray();
            var vertices = new List<Vector3>();
            var uvs = new List<Vector2>();
            var lidUv = new List<Vector2>();   // UV1: 同じ側の白目のアトラス上の UV(この頂点のキャンバス位置)
            var lidInfo = new List<Vector4>(); // UV2: (目 1=l 2=r 0=対象外, 1px あたりの UV の高さ, 白目の UV 上端, 白目の影 1 / 瞳のハイライト 2)
            var lidRect = new List<Vector4>(); // UV3: 白目の UV の左端・右端・下端
            var exprUv = new List<Vector4>();  // UV4: (表示する表情のビット, 口の開き具合の範囲 2 つ, 目の差し替え)
            var weights = new List<BoneWeight>();
            var triangles = new List<int>();
            for (var n = 0; n < bones.Length; n++)
            {
                var i = draw[n] < n0 ? draw[n] : fronts[draw[n] - n0];  // 手前用の複製は元のレイヤーの絵
                var b = loaded.Ordered[i].bbox;
                var r = rects[i];
                var z = new Vector3(0f, 0f, n * s.LayerSpacing);
                var start = vertices.Count;
                vertices.Add(ToLocal(b[0], b[1]) + z);  // 左上
                vertices.Add(ToLocal(b[2], b[1]) + z);  // 右上
                vertices.Add(ToLocal(b[0], b[3]) + z);  // 左下
                vertices.Add(ToLocal(b[2], b[3]) + z);  // 右下
                uvs.Add(new Vector2(r.xMin, r.yMax));
                uvs.Add(new Vector2(r.xMax, r.yMax));
                uvs.Add(new Vector2(r.xMin, r.yMin));
                uvs.Add(new Vector2(r.xMax, r.yMin));
                for (var v = 0; v < 4; v++) weights.Add(new BoneWeight { boneIndex0 = n, weight0 = 1f });
                AddLidUvs(loaded, rects, i, lidUv, lidInfo, lidRect);
                var expr = Expression.Uv4(doc, loaded.Ordered[i]);
                for (var v = 0; v < 4; v++) exprUv.Add(expr);
                triangles.AddRange(new[] { start, start + 2, start + 1, start + 1, start + 2, start + 3 });
            }

            var mesh = new Mesh { name = $"{s.RigName}_mesh" };
            mesh.SetVertices(vertices);
            mesh.SetUVs(0, uvs);
            mesh.SetUVs(1, lidUv);
            mesh.SetUVs(2, lidInfo);
            mesh.SetUVs(3, lidRect);
            mesh.SetUVs(4, exprUv);
            mesh.SetNormals(Enumerable.Repeat(Vector3.forward, vertices.Count).ToList());
            mesh.SetTriangles(triangles, 0);
            mesh.boneWeights = weights.ToArray();
            mesh.bindposes = bones.Select(bone => bone.worldToLocalMatrix * meshObject.localToWorldMatrix).ToArray();
            mesh.RecalculateBounds();
            mesh = SaveMesh(mesh, meshPath);

            var smr = meshObject.gameObject.AddComponent<SkinnedMeshRenderer>();
            smr.sharedMesh = mesh;
            smr.sharedMaterial = material;
            smr.bones = bones;
            smr.rootBone = bodyRoot;
            smr.updateWhenOffscreen = false;
            smr.shadowCastingMode = ShadowCastingMode.Off;
            smr.receiveShadows = false;
            smr.lightProbeUsage = LightProbeUsage.Off;
            smr.reflectionProbeUsage = ReflectionProbeUsage.Off;
            smr.skinnedMotionVectors = false;

            // 境界はルートボーン基準。動きの分の余裕を持たせる
            var bounds = new Bounds(bodyRoot.InverseTransformPoint(meshObject.TransformPoint(vertices[0])), Vector3.zero);
            foreach (var v in vertices) bounds.Encapsulate(bodyRoot.InverseTransformPoint(meshObject.TransformPoint(v)));
            bounds.Expand(0.2f + (limbs != null ? 2f * LimbRig.Reach(limbs, k) : 0f));  // 手足は根元を中心に回る
            smr.localBounds = bounds;

            var animator = root.GetComponent<Animator>();
            if (limbs == null)
            {
                animator.avatar = SaveGenericAvatar(root, avatarPath);
                return root;
            }
            animator.avatar = LimbRig.Build(root, bodyRoot, headRoot, limbs, jointNodes, ToLocalV, s.BottomY, NewChild,
                avatarPath, errors, warnings);
            if (animator.avatar != null)
            {
                foreach (var front in frontNodes) front.localScale = Vector3.zero;  // Avatar を作ってから隠す(FX が動かしている間だけ表示)
                return root;
            }
            Object.DestroyImmediate(root);
            return null;
        }

        /// <summary>片目のまばたきの寸法を白目・まつ毛の素材から測る。白目が無ければ valid=false。</summary>
        static EyeLidInfo MeasureEye(LoadedLayers loaded, string side)
        {
            var white = System.Array.FindIndex(loaded.Ordered, l => l.role == $"eye_{side}_white");
            if (white < 0) return new EyeLidInfo();
            var lash = System.Array.FindIndex(loaded.Ordered, l => l.role == $"eye_{side}_lash");
            return EyeLid.Measure(loaded.Ordered[white], loaded.Textures[white],
                lash >= 0 ? loaded.Ordered[lash] : null, lash >= 0 ? loaded.Textures[lash] : null);
        }

        /// <summary>
        /// レイヤー i の 4 頂点(左上・右上・左下・右下)ぶんの切り取り用 UV を足す。
        /// 白目・黒目・白目の影・瞳のハイライトなら同じ側の白目のアトラス上の位置(静止時)を、それ以外は 0 を入れる(シェーダは目 0 を切り取らない)。
        /// </summary>
        static void AddLidUvs(LoadedLayers loaded, Rect[] rects, int i, List<Vector2> lidUv, List<Vector4> lidInfo,
            List<Vector4> lidRect)
        {
            var layer = loaded.Ordered[i];
            var side = layer.role == "eye_l_white" || layer.role == "eye_l_pupil" || layer.role == "eye_l_shadow" ||
                       layer.role == "eye_l_highlight" ? "l"
                : layer.role == "eye_r_white" || layer.role == "eye_r_pupil" || layer.role == "eye_r_shadow" ||
                  layer.role == "eye_r_highlight" ? "r" : null;
            var shadow = layer.role.EndsWith("_shadow") ? 1f : IsHighlight(layer) ? 2f : 0f;
            var w = side == null ? -1 : System.Array.FindIndex(loaded.Ordered, l => l.role == $"eye_{side}_white");
            if (w < 0)
            {
                for (var v = 0; v < 4; v++)
                {
                    lidUv.Add(Vector2.zero);
                    lidInfo.Add(Vector4.zero);
                    lidRect.Add(Vector4.zero);
                }
                return;
            }
            var wb = loaded.Ordered[w].bbox;
            var wr = rects[w];
            var b = layer.bbox;
            Vector2 WhiteUv(float px, float py) => new Vector2(
                wr.xMin + (px - wb[0]) / (wb[2] - wb[0]) * wr.width,
                wr.yMin + (wb[3] - py) / (wb[3] - wb[1]) * wr.height);
            lidUv.Add(WhiteUv(b[0], b[1]));
            lidUv.Add(WhiteUv(b[2], b[1]));
            lidUv.Add(WhiteUv(b[0], b[3]));
            lidUv.Add(WhiteUv(b[2], b[3]));
            var info = new Vector4(side == "l" ? 1f : 2f, wr.height / (wb[3] - wb[1]), wr.yMax, shadow);
            for (var v = 0; v < 4; v++)
            {
                lidInfo.Add(info);
                lidRect.Add(new Vector4(wr.xMin, wr.xMax, wr.yMin, 0f));
            }
        }

        /// <summary>目に付いて動く役割(黒目・まつ毛・白目の影は白目の子、瞳のハイライトは黒目の子。黒目が無ければ白目の子)。</summary>
        public static bool IsEyeAttached(LayerDef layer) =>
            layer.role == "eye_l_pupil" || layer.role == "eye_r_pupil" || layer.role == "eye_l_lash" || layer.role == "eye_r_lash" ||
            layer.role == "eye_l_shadow" || layer.role == "eye_r_shadow" || IsHighlight(layer);

        public static bool IsHighlight(LayerDef layer) => layer.role == "eye_l_highlight" || layer.role == "eye_r_highlight";

        /// <summary>"eye_l_highlight" → "eye_l_pupil"。</summary>
        public static string EyePupilRole(string role) => role.Substring(0, "eye_l".Length) + "_pupil";

        /// <summary>"eye_l_pupil" → "eye_l_white"。</summary>
        public static string EyeWhiteRole(string role) => role.Substring(0, "eye_l".Length) + "_white";

        static bool HasPivot(float[] pivot) => pivot != null && pivot.Length == 2;

        static float[] DefaultHeadPivot(LayersDoc doc, float cw, float ch)
        {
            // 顔は PSD のレイヤーごとに分かれていることがある(§5.12)。飾り(奥行きのあるもの)を除いた顔全体の下端中央
            var faces = doc.layers.Where(l => l.role == "face").ToArray();
            if (faces.Length == 0) return new[] { cw * 0.5f, ch * 0.5f };
            if (faces.Length == 1) return new[] { faces[0].pivot[0], faces[0].bbox[3] };
            var flat = faces.Where(l => l.depth == 0f).ToArray();
            if (flat.Length > 0) faces = flat;
            return new[] { (faces.Min(l => l.bbox[0]) + faces.Max(l => l.bbox[2])) * 0.5f, faces.Max(l => l.bbox[3]) };
        }

        /// <summary>ルートからの相対位置(= ルートを原点に置いたときのワールド位置)に子を作る。</summary>
        internal static Transform NewChild(string name, Transform parent, Vector3 rootLocalPosition)
        {
            var t = new GameObject(name).transform;
            t.SetParent(parent, false);
            t.position = parent.root.TransformPoint(rootLocalPosition);
            return t;
        }

        static Texture2D SaveAtlas(Texture2D atlas, string assetPath)
        {
            File.WriteAllBytes(ToFullPath(assetPath), ImageConversion.EncodeToPNG(atlas));
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
            var importer = (TextureImporter)AssetImporter.GetAtPath(assetPath);
            importer.textureType = TextureImporterType.Default;
            importer.alphaSource = TextureImporterAlphaSource.FromInput;
            importer.alphaIsTransparency = true;  // 透明部分の色を周囲で埋め、縁の黒ずみを防ぐ
            importer.npotScale = TextureImporterNPOTScale.None;
            importer.mipmapEnabled = true;
            importer.streamingMipmaps = true;  // VRChat SDK がミップマップ付きテクスチャに必須としている
            importer.mipmapFilter = TextureImporterMipFilter.KaiserFilter;  // 遠目でぼやけにくい(SDK の推奨)
            importer.wrapMode = TextureWrapMode.Clamp;
            importer.maxTextureSize = Mathf.Clamp(Mathf.NextPowerOfTwo(Mathf.Max(atlas.width, atlas.height)), 32, 8192);
            importer.textureCompression = TextureImporterCompression.CompressedHQ;
            importer.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
        }

        /// <summary>既存のアセットは中身だけ差し替える(GUID を保ち、他からの参照を切らないため)。</summary>
        static Material SaveMaterial(Shader shader, Texture2D atlas, string assetPath)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            if (material == null)
            {
                material = new Material(shader);
                AssetDatabase.CreateAsset(material, assetPath);
            }
            material.shader = shader;
            material.mainTexture = atlas;
            material.renderQueue = -1;  // シェーダの Queue(Transparent)に従う
            // 表情・絵の差し替えの中立の値(アニメータが無くても通常の表情・口を閉じ・目を開いた見た目になる)
            for (var i = 0; i < Expression.WeightProps.Length; i++) material.SetVector(Expression.WeightProps[i], Expression.Weights(0, i));
            material.SetFloat(Expression.MouthOpenProp, 0f);
            material.SetFloat(Expression.EyeOpenPropL, 1f);
            material.SetFloat(Expression.EyeOpenPropR, 1f);
            material.SetFloat(Expression.EyeClosedAtProp, Expression.DefaultEyeClosedAt);
            EditorUtility.SetDirty(material);
            return material;
        }

        /// <summary>
        /// Generic の Avatar を作って保存する。Avatar の無い Animator だと VRChat 上でアニメーションが動かないことがあるため。
        /// Avatar は階層の写しなので、作り直すたびに置き換える。
        /// </summary>
        static Avatar SaveGenericAvatar(GameObject root, string assetPath)
        {
            var avatar = AvatarBuilder.BuildGenericAvatar(root, "");
            avatar.name = Path.GetFileNameWithoutExtension(assetPath);
            if (AssetDatabase.LoadAssetAtPath<Avatar>(assetPath) != null) AssetDatabase.DeleteAsset(assetPath);
            AssetDatabase.CreateAsset(avatar, assetPath);
            return avatar;
        }

        static Mesh SaveMesh(Mesh mesh, string assetPath)
        {
            var existing = AssetDatabase.LoadAssetAtPath<Mesh>(assetPath);
            if (existing == null)
            {
                AssetDatabase.CreateAsset(mesh, assetPath);
                return mesh;
            }
            // EditorUtility.CopySerialized は頂点データを写さず、前回の頂点・UV が残って見た目が崩れたため、
            // 中身を API で入れ直す(GUID を保つため、アセット自体は作り直さない)
            existing.Clear();
            existing.SetVertices(mesh.vertices);
            existing.SetNormals(mesh.normals);
            existing.SetUVs(0, mesh.uv);
            for (var ch = 1; ch <= 4; ch++)
            {
                var list = new List<Vector4>();
                mesh.GetUVs(ch, list);
                if (list.Count > 0) existing.SetUVs(ch, list);
            }
            existing.boneWeights = mesh.boneWeights;
            existing.bindposes = mesh.bindposes;
            existing.SetTriangles(mesh.triangles, 0);
            existing.RecalculateBounds();
            existing.name = Path.GetFileNameWithoutExtension(assetPath);
            Object.DestroyImmediate(mesh);
            EditorUtility.SetDirty(existing);
            return existing;
        }

        static void EnsureFolder(string assetFolder)
        {
            var parts = assetFolder.Split('/');
            var current = parts[0];
            for (var i = 1; i < parts.Length; i++)
            {
                var next = $"{current}/{parts[i]}";
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }

        public static string ToFullPath(string assetPath) =>
            Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", assetPath);
    }
}
