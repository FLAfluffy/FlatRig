using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
#if FLATRIG_VRCSDK
using VRC.Dynamics;
using VRC.SDK3.Dynamics.Constraint.Components;
#endif

namespace FlatRig.Editor
{
    /// <summary>
    /// 手足をトラッキングに合わせる(docs/SPEC.md §5.10)。生成ウィンドウの設定が ON のときだけ使う。
    ///
    /// 仕組み:
    /// 1. 見えない人型の骨格(FR_Humanoid、T ポーズ)を作り、アバターを Humanoid にする。VRChat の IK・アニメーションがこれを動かす。
    /// 2. 体ルートの下に、人型の腕・脚と同じ形の「影の骨格」(FR_LimbShadow)を、肩・股関節を絵の位置に合わせて作り、
    ///    人型の骨の回転をローカル空間で写す(VRC Rotation Constraint、Solve In Local Space)。
    /// 3. 影の関節を絵の平面に投影したノード(FR_LimbAim_*)を作る(VRC Position Constraint、体ルート基準の Z を固定)。
    /// 4. 2D の手足のパーツ(関節ノード {role}_J)を、投影した関節の方向へ向ける(VRC Aim Constraint、回転は絵の平面内だけ)。
    ///    上腕・太ももは投影した肘・膝を直接狙う。前腕・手などは、根元の位置が絵と影でずれるため、
    ///    投影した根元のノードで向きを求めて、その回転を写す。
    /// 手足の役割の _l / _r は画面の左右(画面の左 = キャラクターの右手・右脚)。
    /// </summary>
    public static class LimbRig
    {
        public const string JointSuffix = "_J";
        public const string HumanoidName = "FR_Humanoid";
        public const string ShadowName = "FR_LimbShadow";
        public const string AimHelperPrefix = "FR_LimbAim_";
        const float EndSlab = 0.2f;              // パーツの端の位置: 端から長さの 20% までの不透明部分の重心
        const int MaxSamples = 40000;            // 関節を求めるときに見る画素数の上限(大きなパーツは間引く)
        const float FootHeight = 0.08f;          // 足首の高さ(m)。描いた脚が床に届かないときは人型の脚をこの高さまで伸ばす

        /// <summary>手足の役割。上から順に 根元 → 先。</summary>
        public static readonly string[] Roles =
        {
            "arm_l_upper", "arm_l_lower", "hand_l", "arm_r_upper", "arm_r_lower", "hand_r",
            "leg_l_upper", "leg_l_lower", "foot_l", "leg_r_upper", "leg_r_lower", "foot_r",
        };

        static readonly (string name, bool arm, bool screenLeft, string[] roles, string label)[] Defs =
        {
            ("arm_l", true, true, new[] { "arm_l_upper", "arm_l_lower", "hand_l" }, "腕(画面左)"),
            ("arm_r", true, false, new[] { "arm_r_upper", "arm_r_lower", "hand_r" }, "腕(画面右)"),
            ("leg_l", false, true, new[] { "leg_l_upper", "leg_l_lower", "foot_l" }, "脚(画面左)"),
            ("leg_r", false, false, new[] { "leg_r_upper", "leg_r_lower", "foot_r" }, "脚(画面右)"),
        };

        public static bool IsLimbRole(string role) => Array.IndexOf(Roles, role) >= 0;

        /// <summary>関節ノード {role}_J に付いた Constraint(役割, ノード, 型)。</summary>
        public static List<(string role, Transform node, Type type)> JointConstraints(Transform root)
        {
            var list = new List<(string, Transform, Type)>();
#if FLATRIG_VRCSDK
            foreach (var c in root.GetComponentsInChildren<VRCConstraintBase>(true))
            {
                var name = c.gameObject.name;
                if (!name.EndsWith(JointSuffix)) continue;
                var role = name.Substring(0, name.Length - JointSuffix.Length);
                if (IsLimbRole(role)) list.Add((role, c.transform, c.GetType()));
            }
#endif
            return list;
        }

        /// <summary>腕と手の役割か(false なら脚と足)。</summary>
        public static bool IsArmRole(string role) => Defs.Any(d => d.arm && Array.IndexOf(d.roles, role) >= 0);

        /// <summary>
        /// 固定のトグル(§5.10)で重みを 0 にする Constraint の場所と型。関節ノード {role}_J に付いたものだけ。
        /// arms = true なら腕と手、false なら脚と足。重み 0 の関節ノードは絵の初期の向きに戻る。
        /// </summary>
        public static List<(Transform node, Type type)> FreezeTargets(Transform root, bool arms) =>
            JointConstraints(root).Where(j => IsArmRole(j.role) == arms).Select(j => (j.node, j.type)).ToList();

        /// <summary>
        /// 手の追従(§5.11)の 2 関節 IK。絵の平面(ルート基準の XY、m)で、肩 shoulder から手首を target へ伸ばす。
        /// elbow・wrist は絵の肘・手首。twoBones = false(前腕が無い)なら上腕だけを target へ向ける(elbow は上腕の先)。
        /// side は外側の向き(キャラクターの右腕 +1、左腕 −1)。肘は外側・下側に曲げる。届かなければ伸ばしきる。
        /// 戻り値は、関節ノードの Z 回転(度): 上腕は絵の向きから、前腕は上腕から見た絵の向きから。
        /// </summary>
        public static (float upper, float lower) HandIK(Vector2 shoulder, Vector2 elbow, Vector2 wrist, bool twoBones, float side, Vector2 target)
        {
            var upperRest = elbow - shoulder;
            var d = target - shoulder;
            if (d.magnitude < 1e-5f) d = new Vector2(side, -1f);
            if (!twoBones) return (SignedDeg(upperRest, d), 0f);

            var lowerRest = wrist - elbow;
            float l1 = upperRest.magnitude, l2 = lowerRest.magnitude;
            if (l1 < 1e-5f || l2 < 1e-5f) return (SignedDeg(upperRest + lowerRest, d), 0f);
            var dir = d / d.magnitude;
            var dist = Math.Min(Math.Max(d.magnitude, Math.Abs(l1 - l2) + 1e-4f), l1 + l2 - 1e-4f);
            var a = (float)Math.Acos(Math.Min(1.0, Math.Max(-1.0, (l1 * l1 + dist * dist - l2 * l2) / (2.0 * l1 * dist))));
            var prefer = new Vector2(side * 0.3f, -1f);  // 肘を曲げる向き(下、少し外側。切り替わる境目を手が肩の真下に来たときにする)
            var u1 = Rotate(dir, a);
            var u2 = Rotate(dir, -a);
            var upperDir = Vector2.Dot(u1, prefer) >= Vector2.Dot(u2, prefer) ? u1 : u2;
            var e = shoulder + upperDir * l1;
            var w = shoulder + dir * dist;
            var upper = SignedDeg(upperRest, upperDir);
            var lower = Wrap180(SignedDeg(lowerRest, w - e) - upper);
            return (upper, lower);
        }

        static Vector2 Rotate(Vector2 v, float rad)
        {
            float c = (float)Math.Cos(rad), s = (float)Math.Sin(rad);
            return new Vector2(v.x * c - v.y * s, v.x * s + v.y * c);
        }

        /// <summary>a から b への回転(度。反時計回りが正 = Unity の Z 回転)。</summary>
        static float SignedDeg(Vector2 a, Vector2 b) =>
            Wrap180((float)((Math.Atan2(b.y, b.x) - Math.Atan2(a.y, a.x)) * 180.0 / Math.PI));

        public static float Wrap180(float deg)
        {
            deg %= 360f;
            if (deg > 180f) deg -= 360f;
            if (deg <= -180f) deg += 360f;
            return deg;
        }

        /// <summary>腕 1 本・脚 1 本ぶんの、絵の上の関節。</summary>
        public class Limb
        {
            public string Name;        // "arm_l" など
            public string Label;
            public bool IsArm;
            public bool ScreenLeft;    // 画面の左(= キャラクターの右。Unity では +X)
            public string[] Segments;  // 素材にある部品の役割(根元 → 先)
            public int[] Kinds;        // 0 = 上腕・太もも、1 = 前腕・すね、2 = 手・足
            public Vector2[] Joints;   // キャンバス px。Joints[i] は Segments[i] の根元、最後の要素は先端

            public bool Has(int kind) => Array.IndexOf(Kinds, kind) >= 0;
        }

        // --- 絵から関節を求める ------------------------------------------------------

        /// <summary>
        /// 素材の手足のパーツから関節の位置を求める。上腕・太ももが無い手足は使わない(warnings に理由)。
        /// 隣り合う部品の関節は、2 枚の不透明部分が重なる所の重心(重ならなければ一番近い点の中間)。
        /// 根元(肩・股関節)と先端は、パーツの反対側の端の重心。部品が 1 つだけなら上の端を根元にする。
        /// レイヤーに joint([px, py])があれば、その部品の根元はその位置にする。
        /// </summary>
        public static List<Limb> Estimate(LoadedLayers loaded, List<string> warnings)
        {
            var limbs = new List<Limb>();
            foreach (var def in Defs)
            {
                var present = def.roles.Select((r, kind) => (r, kind))
                    .Where(x => loaded.Ordered.Any(l => l.role == x.r)).ToArray();
                if (present.Length == 0) continue;
                if (present[0].kind != 0)
                {
                    warnings.Add($"{def.label}: {def.roles[0]} が無いので、この{(def.arm ? "腕" : "脚")}はトラッキングに合わせません(体と一緒に動きます)");
                    continue;
                }

                var masks = present.Select(x => Mask.Of(loaded, x.r)).ToArray();
                var n = present.Length;
                var joints = new Vector2[n + 1];
                if (n == 1)
                {
                    var (top, bottom) = Ends(masks[0].Samples());
                    joints[0] = top;
                    joints[1] = bottom;
                }
                else
                {
                    for (var i = 0; i < n - 1; i++) joints[i + 1] = Connection(masks[i], masks[i + 1]);
                    joints[0] = FarEnd(masks[0].Samples(), joints[1]);
                    joints[n] = FarEnd(masks[n - 1].Samples(), joints[n - 1]);
                }

                for (var i = 0; i < n; i++)
                {
                    var manual = loaded.Ordered.FirstOrDefault(l => l.role == present[i].r && l.joint != null && l.joint.Length == 2);
                    if (manual != null) joints[i] = new Vector2(manual.joint[0], manual.joint[1]);
                }

                var tooShort = -1;  // 短すぎる部品(-1 = 無し)
                for (var i = 0; i < n && tooShort < 0; i++)
                    if ((joints[i + 1] - joints[i]).magnitude < 2f) tooShort = i;
                if (tooShort >= 0)
                {
                    warnings.Add($"{def.label}: {present[tooShort].r} の関節の位置が求められなかったので、この{(def.arm ? "腕" : "脚")}はトラッキングに合わせません");
                    continue;
                }

                limbs.Add(new Limb
                {
                    Name = def.name, Label = def.label, IsArm = def.arm, ScreenLeft = def.screenLeft,
                    Segments = present.Select(x => x.r).ToArray(),
                    Kinds = present.Select(x => x.kind).ToArray(),
                    Joints = joints,
                });
            }
            return limbs;
        }

        /// <summary>同じ役割のレイヤーをまとめた不透明部分(キャンバス px の格子)。</summary>
        class Mask
        {
            int x0, y0, w, h;
            bool[] on;
            int count;

            public static Mask Of(LoadedLayers loaded, string role)
            {
                var idx = Enumerable.Range(0, loaded.Ordered.Length).Where(i => loaded.Ordered[i].role == role).ToArray();
                var m = new Mask
                {
                    x0 = Mathf.FloorToInt(idx.Min(i => loaded.Ordered[i].bbox[0])),
                    y0 = Mathf.FloorToInt(idx.Min(i => loaded.Ordered[i].bbox[1])),
                };
                m.w = Mathf.CeilToInt(idx.Max(i => loaded.Ordered[i].bbox[2])) - m.x0;
                m.h = Mathf.CeilToInt(idx.Max(i => loaded.Ordered[i].bbox[3])) - m.y0;
                m.on = new bool[m.w * m.h];
                foreach (var i in idx)
                {
                    var tex = loaded.Textures[i];
                    var px = tex.GetPixels32();
                    var bx = Mathf.RoundToInt(loaded.Ordered[i].bbox[0]) - m.x0;
                    var by = Mathf.RoundToInt(loaded.Ordered[i].bbox[1]) - m.y0;
                    for (var ty = 0; ty < tex.height; ty++)
                    {
                        var cy = by + (tex.height - 1 - ty);  // テクスチャの行 0 は下端
                        if (cy < 0 || cy >= m.h) continue;
                        for (var tx = 0; tx < tex.width; tx++)
                        {
                            var cx = bx + tx;
                            if (cx < 0 || cx >= m.w || px[ty * tex.width + tx].a < 128) continue;
                            var k = cy * m.w + cx;
                            if (!m.on[k]) m.count++;
                            m.on[k] = true;
                        }
                    }
                }
                return m;
            }

            public bool Get(Vector2 p)
            {
                var x = Mathf.FloorToInt(p.x) - x0;
                var y = Mathf.FloorToInt(p.y) - y0;
                return x >= 0 && y >= 0 && x < w && y < h && on[y * w + x];
            }

            /// <summary>不透明な画素の中心(キャンバス px)。多いときは格子状に間引く。</summary>
            public List<Vector2> Samples(int max = MaxSamples)
            {
                var step = Mathf.Max(1, Mathf.CeilToInt(Mathf.Sqrt(count / (float)max)));
                var list = new List<Vector2>();
                for (var y = 0; y < h; y += step)
                for (var x = 0; x < w; x += step)
                    if (on[y * w + x]) list.Add(new Vector2(x0 + x + 0.5f, y0 + y + 0.5f));
                return list;
            }
        }

        static Vector2 Centroid(IReadOnlyCollection<Vector2> ps)
        {
            var sum = Vector2.zero;
            foreach (var p in ps) sum += p;
            return ps.Count > 0 ? sum / ps.Count : Vector2.zero;
        }

        /// <summary>2 つの部品のつなぎ目。重なりの重心、重ならなければ一番近い 2 点の中間。</summary>
        static Vector2 Connection(Mask a, Mask b)
        {
            var overlap = a.Samples().Where(b.Get).ToList();
            if (overlap.Count >= 3) return Centroid(overlap);
            var sa = a.Samples(2000);
            var sb = b.Samples(2000);
            var best = float.MaxValue;
            var mid = Vector2.zero;
            foreach (var p in sa)
            foreach (var q in sb)
            {
                var d = (p - q).sqrMagnitude;
                if (d >= best) continue;
                best = d;
                mid = (p + q) * 0.5f;
            }
            return mid;
        }

        /// <summary>from から見て反対側の端(端から長さの EndSlab までの重心)。</summary>
        static Vector2 FarEnd(List<Vector2> samples, Vector2 from)
        {
            if (samples.Count == 0) return from;
            var u = Centroid(samples) - from;
            u = u.sqrMagnitude > 1e-6f ? u.normalized : Vector2.up;  // キャンバスの +y は下
            return Slab(samples, from, u);
        }

        static Vector2 Slab(List<Vector2> samples, Vector2 origin, Vector2 u)
        {
            var t = samples.Select(p => Vector2.Dot(p - origin, u)).ToArray();
            float tmin = t.Min(), tmax = t.Max();
            var limit = tmax - EndSlab * (tmax - tmin);
            return Centroid(samples.Where((p, i) => t[i] >= limit).ToList());
        }

        /// <summary>部品 1 つだけのとき: 長い方向の両端。上(キャンバスの y が小さい方)を先に返す。</summary>
        static (Vector2 top, Vector2 bottom) Ends(List<Vector2> samples)
        {
            var c = Centroid(samples);
            float sxx = 0, sxy = 0, syy = 0;
            foreach (var p in samples)
            {
                var d = p - c;
                sxx += d.x * d.x;
                sxy += d.x * d.y;
                syy += d.y * d.y;
            }
            var angle = 0.5f * Mathf.Atan2(2f * sxy, sxx - syy);  // 分散が最大の向き
            var u = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle));
            var e1 = Slab(samples, c, u);
            var e2 = Slab(samples, c, -u);
            return e1.y <= e2.y ? (e1, e2) : (e2, e1);
        }

        // --- 階層を作る --------------------------------------------------------------

        /// <summary>
        /// 手足の関節ノード({role}_J)を体ルートの下に作る。前腕は上腕の、手は前腕(無ければ上腕)の子。
        /// 戻り値は 役割 → 関節ノード。その役割のレイヤーの視差ノードは、この下に入れる。
        /// </summary>
        public static Dictionary<string, Transform> CreateJointNodes(List<Limb> limbs, Transform bodyRoot,
            Func<Vector2, Vector3> toLocal, Func<string, Transform, Vector3, Transform> newChild)
        {
            var nodes = new Dictionary<string, Transform>();
            foreach (var limb in limbs)
            {
                var parent = bodyRoot;
                for (var i = 0; i < limb.Segments.Length; i++)
                {
                    parent = newChild(limb.Segments[i] + JointSuffix, parent, toLocal(limb.Joints[i]));
                    nodes[limb.Segments[i]] = parent;
                }
            }
            return nodes;
        }

        /// <summary>手足が描かれている範囲の最大の長さ(m)。メッシュの境界の余裕に使う。</summary>
        public static float Reach(List<Limb> limbs, float metersPerPx)
        {
            var reach = 0f;
            foreach (var limb in limbs)
            {
                var len = 0f;
                for (var i = 0; i + 1 < limb.Joints.Length; i++) len += (limb.Joints[i + 1] - limb.Joints[i]).magnitude;
                reach = Mathf.Max(reach, len * metersPerPx);
            }
            return reach;
        }

        /// <summary>人型の手足 1 本(T ポーズ、ルート基準の位置)。P[0] 根元、P[1] 肘・膝、P[2] 手首・足首、P[3] 先端。</summary>
        class ProxyLimb
        {
            public Vector3[] P = new Vector3[4];
            public Transform[] Bones = new Transform[3];  // 人型の 上腕/前腕/手(太もも/すね/足)
            public Vector3 ShoulderPos;                   // 腕だけ: 肩の位置
            public Transform Shoulder;
        }

        /// <summary>
        /// 見えない人型の骨格・影の骨格・Constraint を作り、Humanoid の Avatar を保存して返す。失敗時は null(理由は errors)。
        /// root はルート(原点)、toLocal はキャンバス px → ルート基準の位置。
        /// </summary>
        public static Avatar Build(GameObject root, Transform bodyRoot, Transform headRoot, List<Limb> limbs,
            Dictionary<string, Transform> jointNodes, Func<Vector2, Vector3> toLocal, float bottomY,
            Func<string, Transform, Vector3, Transform> newChild, string avatarPath, List<string> errors, List<string> warnings)
        {
#if FLATRIG_VRCSDK
            var head = root.transform.InverseTransformPoint(headRoot.position);
            Limb Find(string name) => limbs.FirstOrDefault(l => l.Name == name);

            // 腰の位置: 描いた脚があれば股関節の高さ、無ければキャンバス下端(頭より 25cm 以上下)
            var legL = Find("leg_l");
            var legR = Find("leg_r");
            var hipJoints = new[] { legL, legR }.Where(l => l != null).Select(l => toLocal(l.Joints[0])).ToArray();
            var hipsY = hipJoints.Length > 0 ? hipJoints.Average(p => p.y) : bottomY;
            hipsY = Mathf.Clamp(hipsY, Mathf.Min(0.5f, head.y - 0.25f), head.y - 0.25f);
            var hips = new Vector3(head.x, hipsY, 0f);
            var spine = Vector3.Lerp(hips, head, 0.25f);
            var chest = Vector3.Lerp(hips, head, 0.6f);
            var neck = Vector3.Lerp(hips, head, 0.9f);

            // 画面の左(_l)はキャラクターの右手・右脚で +X、画面の右(_r)はキャラクターの左で −X
            var proxy = new Dictionary<string, ProxyLimb>
            {
                ["arm_l"] = ProxyArm(Find("arm_l"), +1f, neck, toLocal),
                ["arm_r"] = ProxyArm(Find("arm_r"), -1f, neck, toLocal),
                ["leg_l"] = ProxyLeg(Find("leg_l"), +1f, hips, toLocal),
                ["leg_r"] = ProxyLeg(Find("leg_r"), -1f, hips, toLocal),
            };

            // 人型の骨格(回転はすべて単位、T ポーズ)
            var humanoid = newChild(HumanoidName, root.transform, Vector3.zero);
            var tHips = newChild("FR_Hips", humanoid, hips);
            var tSpine = newChild("FR_Spine", tHips, spine);
            var tChest = newChild("FR_Chest", tSpine, chest);
            var tNeck = newChild("FR_Neck", tChest, neck);
            var tHead = newChild("FR_Head", tNeck, head);
            var human = new List<HumanBone>();
            void Map(string humanName, Transform t) =>
                human.Add(new HumanBone { humanName = humanName, boneName = t.name, limit = new HumanLimit { useDefaultValues = true } });
            Map("Hips", tHips);
            Map("Spine", tSpine);
            Map("Chest", tChest);
            Map("Neck", tNeck);
            Map("Head", tHead);
            foreach (var (key, side, arm) in new[] { ("arm_l", "Right", true), ("arm_r", "Left", true), ("leg_l", "Right", false), ("leg_r", "Left", false) })
            {
                var p = proxy[key];
                var names = arm ? new[] { "UpperArm", "LowerArm", "Hand" } : new[] { "UpperLeg", "LowerLeg", "Foot" };
                var parent = arm ? tChest : tHips;
                if (arm)
                {
                    // 肩(VRChat の人型は Chest 直下の Shoulder が必須)。胸の中心と上腕の根元の間、上腕と同じ高さに置く
                    p.ShoulderPos = new Vector3(chest.x + (p.P[0].x - chest.x) * 0.4f, p.P[0].y, 0f);
                    p.Shoulder = newChild($"FR_{side}Shoulder", tChest, p.ShoulderPos);
                    Map($"{side}Shoulder", p.Shoulder);
                    parent = p.Shoulder;
                }
                for (var i = 0; i < 3; i++)
                {
                    p.Bones[i] = newChild($"FR_{side}{names[i]}", parent, p.P[i]);
                    Map($"{side}{names[i]}", p.Bones[i]);
                    parent = p.Bones[i];
                }
            }

            // 影の骨格: 人型の腕・脚と同じ形で、肩・股関節を絵の位置に固定したもの(体ルートの下。プレイヤーが動いても絵からずれない)
            var shadowRoot = newChild(ShadowName, bodyRoot, Vector3.zero);
            var shadowChest = newChild("FR_LimbShadow_Chest", shadowRoot, chest);
            var shadowHips = newChild("FR_LimbShadow_Hips", shadowRoot, hips);
            var shadows = new Dictionary<string, Transform[]>();
            var shadowShoulders = new Dictionary<string, Transform>();
            var projected = new Dictionary<string, Transform[]>();
            foreach (var limb in limbs)
            {
                var p = proxy[limb.Name];
                var sh = new Transform[4];
                var parent = limb.IsArm ? shadowChest : shadowHips;
                if (limb.IsArm)
                {
                    // 肩の回転も写す(上腕の回転は肩から見た値なので)
                    shadowShoulders[limb.Name] = newChild($"FR_LimbShadow_{limb.Name}_S", parent, p.ShoulderPos);
                    parent = shadowShoulders[limb.Name];
                }
                for (var j = 0; j < 4; j++)
                {
                    sh[j] = newChild($"FR_LimbShadow_{limb.Name}_{j}", parent, p.P[j]);
                    parent = sh[j];
                }
                shadows[limb.Name] = sh;

                // 投影ノード: 影の関節を絵の平面に投影した位置(部品の根元か、部品が向く先になる関節だけ)
                var q = new Transform[4];
                for (var i = 0; i < limb.Segments.Length; i++)
                {
                    var (from, to) = SegmentJoints(limb, i);
                    if (i > 0 && q[from] == null) q[from] = newChild($"{AimHelperPrefix}{limb.Name}_{from}", bodyRoot, p.P[from]);
                    if (q[to] == null) q[to] = newChild($"{AimHelperPrefix}{limb.Name}_{to}", bodyRoot, p.P[to]);
                }
                projected[limb.Name] = q;
            }

            // Avatar は constraint で動く前の姿勢(T ポーズ)から作る
            var avatar = SaveHumanAvatar(root, human, avatarPath, errors);
            if (avatar == null) return null;

            var count = 0;
            foreach (var limb in limbs)
            {
                var p = proxy[limb.Name];
                var sh = shadows[limb.Name];
                var q = projected[limb.Name];
                if (shadowShoulders.TryGetValue(limb.Name, out var shadowShoulder))
                {
                    AddLocalRotationCopy(shadowShoulder, p.Shoulder);
                    count++;
                }
                for (var j = 0; j < 3; j++) AddLocalRotationCopy(sh[j], p.Bones[j]);
                count += 3;
                for (var j = 0; j < 4; j++)
                {
                    if (q[j] == null) continue;
                    AddProjection(q[j], sh[j]);
                    count++;
                }

                for (var i = 0; i < limb.Segments.Length; i++)
                {
                    var (from, to) = SegmentJoints(limb, i);
                    var axis = (toLocal(limb.Joints[i + 1]) - toLocal(limb.Joints[i])).normalized;  // 絵での部品の向き
                    var node = jointNodes[limb.Segments[i]];
                    if (i == 0)
                    {
                        // 上腕・太ももの根元(肩・股関節)は絵と影で同じ位置なので、直接向ける
                        AddAim(node, q[to], axis, bodyRoot);
                        count++;
                    }
                    else
                    {
                        // 前腕・手などの根元は絵と影で位置がずれるので、投影した根元で向きを求めて回転を写す
                        AddAim(q[from], q[to], axis, bodyRoot);
                        AddRotationCopy(node, q[from]);
                        count += 2;
                    }
                }
            }
            Debug.Log($"[FlatRig] 手足をトラッキングに合わせる: 手足 {limbs.Count} 本、Constraint {count} 個(Performance Rank の Constraint 数に入ります)");
            return avatar;
#else
            errors.Add("手足をトラッキングに合わせるには VRChat SDK(Avatars 3.7.0 以上)が必要です");
            return null;
#endif
        }

        /// <summary>
        /// 腕の T ポーズ。描いた腕があれば肩と各部品の長さを使い、無ければ首から決めた標準の寸法にする。
        /// 前腕が無ければ、上腕〜手首の長さを半分ずつにする。手が無ければ手の長さは前腕の 3 割。
        /// </summary>
        static ProxyLimb ProxyArm(Limb limb, float sx, Vector3 neck, Func<Vector2, Vector3> toLocal)
        {
            var (root, lens) = limb != null
                ? (toLocal(limb.Joints[0]), Lengths(limb, toLocal))
                : (neck + new Vector3(sx * 0.15f, -0.04f, 0f), new[] { 0.25f, 0.23f, 0.08f });
            var p = new ProxyLimb();
            p.P[0] = root;
            for (var i = 0; i < 3; i++) p.P[i + 1] = p.P[i] + new Vector3(sx * lens[i], 0f, 0f);
            return p;
        }

        /// <summary>脚の T ポーズ(真下)。足首が床から離れていれば、太もも・すねを伸ばして足首を FootHeight に合わせる。</summary>
        static ProxyLimb ProxyLeg(Limb limb, float sx, Vector3 hips, Func<Vector2, Vector3> toLocal)
        {
            Vector3 root;
            float[] lens;
            if (limb != null)
            {
                root = toLocal(limb.Joints[0]);
                lens = Lengths(limb, toLocal);
            }
            else
            {
                root = hips + new Vector3(sx * 0.09f, 0f, 0f);
                lens = new[] { 0.5f, 0.5f, 0.08f };
            }
            var legLength = lens[0] + lens[1];
            if (root.y > 0.3f && legLength > 1e-4f)
            {
                var k = Mathf.Clamp((root.y - FootHeight) / legLength, 0.2f, 5f);
                for (var i = 0; i < 3; i++) lens[i] *= k;
            }
            var p = new ProxyLimb();
            p.P[0] = root;
            for (var i = 0; i < 3; i++) p.P[i + 1] = p.P[i] + new Vector3(0f, -lens[i], 0f);
            return p;
        }

        /// <summary>
        /// 部品 i の根元と向く先の、影の関節の番号(0 肩・股関節、1 肘・膝、2 手首・足首、3 先端)。
        /// 前腕・すねが無ければ、上腕・太ももは手首・足首を向く(腕・脚全体が 1 枚)。
        /// </summary>
        static (int from, int to) SegmentJoints(Limb limb, int i)
        {
            var kind = limb.Kinds[i];
            return (kind, kind == 0 && !limb.Has(1) ? 2 : kind + 1);
        }

        /// <summary>上腕・前腕・手(太もも・すね・足)の長さ(m)。</summary>
        static float[] Lengths(Limb limb, Func<Vector2, Vector3> toLocal)
        {
            var seg = new float[3];
            for (var i = 0; i < limb.Segments.Length; i++)
                seg[limb.Kinds[i]] = (toLocal(limb.Joints[i + 1]) - toLocal(limb.Joints[i])).magnitude;
            if (!limb.Has(1))
            {
                seg[1] = seg[0] * 0.5f;
                seg[0] *= 0.5f;
            }
            if (!limb.Has(2)) seg[2] = seg[1] * 0.3f;
            return seg;
        }

        static Avatar SaveHumanAvatar(GameObject root, List<HumanBone> human, string assetPath, List<string> errors)
        {
            var skeleton = root.GetComponentsInChildren<Transform>(true)
                .Select(t => new SkeletonBone { name = t.name, position = t.localPosition, rotation = t.localRotation, scale = t.localScale })
                .ToArray();
            var description = new HumanDescription
            {
                human = human.ToArray(),
                skeleton = skeleton,
                upperArmTwist = 0.5f,
                lowerArmTwist = 0.5f,
                upperLegTwist = 0.5f,
                lowerLegTwist = 0.5f,
                armStretch = 0.05f,
                legStretch = 0.05f,
                feetSpacing = 0f,
                hasTranslationDoF = false,
            };
            var avatar = AvatarBuilder.BuildHumanAvatar(root, description);
            if (avatar == null || !avatar.isValid || !avatar.isHuman)
            {
                errors.Add("人型(Humanoid)の Avatar を作れませんでした。Console に出ている Unity の警告を確認してください");
                if (avatar != null) UnityEngine.Object.DestroyImmediate(avatar);
                return null;
            }
            avatar.name = Path.GetFileNameWithoutExtension(assetPath);
            if (AssetDatabase.LoadAssetAtPath<Avatar>(assetPath) != null) AssetDatabase.DeleteAsset(assetPath);
            AssetDatabase.CreateAsset(avatar, assetPath);
            return avatar;
        }

#if FLATRIG_VRCSDK
        /// <summary>source のローカル回転をそのまま写す(親に対する向き)。</summary>
        static void AddLocalRotationCopy(Transform target, Transform source)
        {
            var c = target.gameObject.AddComponent<VRCRotationConstraint>();
            c.Sources.Add(new VRCConstraintSource(source, 1f));
            c.SolveInLocalSpace = true;
            c.AffectsRotationX = c.AffectsRotationY = c.AffectsRotationZ = true;
            Finish(c);
        }

        /// <summary>source のワールド回転を写す。</summary>
        static void AddRotationCopy(Transform target, Transform source)
        {
            var c = target.gameObject.AddComponent<VRCRotationConstraint>();
            c.Sources.Add(new VRCConstraintSource(source, 1f));
            c.AffectsRotationX = c.AffectsRotationY = c.AffectsRotationZ = true;
            Finish(c);
        }

        /// <summary>
        /// source の位置を、親(体ルート)の Z を固定して写す = 絵の平面への投影。
        /// 固定した軸は親の空間のローカル位置で元の値に戻る(SDK 3.10.5 の VRCConstraintJob で確認)。
        /// </summary>
        static void AddProjection(Transform target, Transform source)
        {
            var c = target.gameObject.AddComponent<VRCPositionConstraint>();
            c.Sources.Add(new VRCConstraintSource(source, 1f));
            c.AffectsPositionX = true;
            c.AffectsPositionY = true;
            c.AffectsPositionZ = false;
            Finish(c);
        }

        /// <summary>
        /// aimAxis(静止時の部品の向き)を source へ向ける。上の向き(ローカル +Z)は体ルートの +Z(絵の正面)に合わせるので、
        /// source が絵の平面にあれば回転は平面内だけになる。
        /// </summary>
        static void AddAim(Transform target, Transform source, Vector3 aimAxis, Transform bodyRoot)
        {
            var c = target.gameObject.AddComponent<VRCAimConstraint>();
            c.Sources.Add(new VRCConstraintSource(source, 1f));
            c.AimAxis = aimAxis;
            c.UpAxis = Vector3.forward;
            c.WorldUp = VRCConstraintBase.WorldUpType.ObjectRotationUp;
            c.WorldUpTransform = bodyRoot;
            c.WorldUpVector = Vector3.forward;
            c.AffectsRotationX = c.AffectsRotationY = c.AffectsRotationZ = true;
            Finish(c);
        }

        static void Finish(VRCConstraintBase c)
        {
            c.IsActive = true;
            c.GlobalWeight = 1f;
            c.Locked = true;  // 静止時・オフセットを 0 のまま固定する(生成時の姿勢で焼き込まない)
            c.ApplyConfigurationChanges();
        }
#endif
    }
}
