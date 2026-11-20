#if FLATRIG_VRCSDK
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using Object = UnityEngine.Object;

namespace FlatRig.Editor
{
    /// <summary>
    /// params.json から Expression Parameters / Menu を生成し、アバターの VRC Avatar Descriptor に設定する(docs/SPEC.md §5.7)。
    /// Descriptor には FX レイヤー(AnimatorGenerator の生成物)と視点位置(両目の中心の少し手前)も設定する。
    /// 生成物は毎回上書きする(利用者が独自に足したパラメータは残らない)。
    /// </summary>
    public static class ExpressionAssets
    {
        public const string MenuLabel = "FlatRig";
        public const string ExprMenuLabel = "表情";
        public const string AutoBlinkMenuLabel = "自動まばたき";
        public const string NextPageLabel = "次へ";
        public const string FreezeArmsMenuLabel = "腕を固定";
        public const string FreezeLegsMenuLabel = "脚を固定";
        public const string HandCamMenuLabel = "手をカメラで動かす";
        static readonly Dictionary<string, string> MenuLabels = new Dictionary<string, string>
        {
            [AnimatorGenerator.FreezeArmsParam] = FreezeArmsMenuLabel,
            [AnimatorGenerator.FreezeLegsParam] = FreezeLegsMenuLabel,
            [AnimatorGenerator.HandCamParam] = HandCamMenuLabel,
        };
        const int MenuSize = 8;  // VRChat のメニュー 1 枚に置ける数
        const float ViewForward = 0.05f;  // 視点を板の手前に置く(m)

        public static string ParametersPath(string rigName) => $"{RigBuilder.OutputFolder(rigName)}/{rigName}_Params.asset";
        public static string MenuPath(string rigName) => $"{RigBuilder.OutputFolder(rigName)}/{rigName}_Menu.asset";

        public static VRCAvatarDescriptor Generate(GameObject root, string paramsPath, List<string> errors, List<string> warnings)
        {
            var doc = ParamsFile.Load(paramsPath, errors);
            if (doc == null) return null;

            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(AnimatorGenerator.ControllerPath(root.name));
            if (controller == null)
            {
                errors.Add("FX 用のアニメータがありません。先に「アニメータを生成」を実行してください");
                return null;
            }

            // 手足の固定・手の追従(アニメータにあるときだけ。Bool 1 bit、Float 8 bit)
            var freeze = controller.parameters.Where(p => AnimatorGenerator.ExtraParams.Contains(p.name)).ToArray();
            var bits = BudgetValidator.SyncedBits(doc) + freeze.Sum(p => p.type == AnimatorControllerParameterType.Float ? 8 : 1);
            if (bits > BudgetValidator.MaxBits)
            {
                errors.Add($"同期パラメータが {bits} bit で、上限 {BudgetValidator.MaxBits} bit を超えています(params.json を見直してください)");
                return null;
            }

            var parameters = SaveParameters(doc, freeze, ParametersPath(root.name));
            var names = doc.flags.Any(f => f.avatar == ParamsFile.ExprFlag && f.IsInt) ? ExpressionNames(root.name) : new string[0];
            var autoBlink = doc.flags.Any(f => f.avatar == ParamsFile.AutoBlinkFlag && !f.IsInt);
            var menu = SaveMenu(root.name, names, autoBlink,
                freeze.Select(p => p.name).Where(n => MenuLabels.ContainsKey(n)).ToArray());

            var descriptor = root.GetComponent<VRCAvatarDescriptor>();
            if (descriptor == null) descriptor = Undo.AddComponent<VRCAvatarDescriptor>(root);
            Undo.RecordObject(descriptor, "FlatRig Expressions");

            descriptor.ViewPosition = ViewPosition(root, warnings);
            descriptor.customExpressions = true;
            descriptor.expressionParameters = parameters;
            descriptor.expressionsMenu = menu;
            if (!SetFxLayer(descriptor, controller))
            {
                errors.Add("Descriptor の FX レイヤーが見つかりません。Hierarchy でアバターを選んで Inspector に Descriptor を表示してから、もう一度実行してください");
                return null;
            }

            EditorUtility.SetDirty(descriptor);
            AssetDatabase.SaveAssets();
            warnings.AddRange(CheckControllerParams(controller, parameters));
            return descriptor;
        }

        static VRCExpressionParameters SaveParameters(ParamsDoc doc, AnimatorControllerParameter[] freeze, string path)
        {
            var list = doc.@params.Select(p => new VRCExpressionParameters.Parameter
            {
                name = p.avatar,
                valueType = VRCExpressionParameters.ValueType.Float,
                saved = false,
                defaultValue = 0f,
                networkSynced = p.synced,
            }).ToList();
            list.AddRange(doc.flags.Select(f => new VRCExpressionParameters.Parameter
            {
                name = f.avatar,
                valueType = f.IsInt ? VRCExpressionParameters.ValueType.Int : VRCExpressionParameters.ValueType.Bool,
                saved = f.avatar == ParamsFile.EnableFlag || f.avatar == ParamsFile.AutoBlinkFlag,  // メニューの ON/OFF はアバターを替えても保つ
                defaultValue = f.avatar == ParamsFile.EnableFlag ? 1f : 0f,
                networkSynced = f.synced,
            }));
            // 手足の固定・手の追従のトグルは、アバターを着るたびに生成時の初期値から始める(saved = false)
            list.AddRange(freeze.Select(p => new VRCExpressionParameters.Parameter
            {
                name = p.name,
                valueType = p.type == AnimatorControllerParameterType.Float
                    ? VRCExpressionParameters.ValueType.Float : VRCExpressionParameters.ValueType.Bool,
                saved = false,
                defaultValue = p.type == AnimatorControllerParameterType.Bool && p.defaultBool ? 1f : 0f,
                networkSynced = true,
            }));

            var asset = LoadOrCreate<VRCExpressionParameters>(path);
            asset.parameters = list.ToArray();
            EditorUtility.SetDirty(asset);
            return asset;
        }

        /// <summary>
        /// FlatRig の ON/OFF、params.json に FR_AutoBlink があれば「自動まばたき」のトグル、
        /// 手足の Constraint があれば「腕を固定」「脚を固定」のトグル、
        /// 表情があれば「表情」のサブメニュー(表情ごとに FR_Expr = 番号 のトグル。OFF で 0 に戻る)。
        /// 表情が 1 枚に収まらなければ、最後の項目を「次へ」にして続きのメニューを作る。
        /// </summary>
        static VRCExpressionsMenu SaveMenu(string rigName, string[] names, bool autoBlink, string[] freeze)
        {
            var asset = LoadOrCreate<VRCExpressionsMenu>(MenuPath(rigName));
            asset.controls = new List<VRCExpressionsMenu.Control>
            {
                new VRCExpressionsMenu.Control
                {
                    name = MenuLabel,
                    type = VRCExpressionsMenu.Control.ControlType.Toggle,
                    parameter = new VRCExpressionsMenu.Control.Parameter { name = ParamsFile.EnableFlag },
                    value = 1f,
                },
            };
            if (autoBlink)
            {
                asset.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = AutoBlinkMenuLabel,
                    type = VRCExpressionsMenu.Control.ControlType.Toggle,
                    parameter = new VRCExpressionsMenu.Control.Parameter { name = ParamsFile.AutoBlinkFlag },
                    value = 1f,
                });
            }
            foreach (var param in freeze)
            {
                asset.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = MenuLabels[param],
                    type = VRCExpressionsMenu.Control.ControlType.Toggle,
                    parameter = new VRCExpressionsMenu.Control.Parameter { name = param },
                    value = 1f,
                });
            }
            if (names.Length > 0)
            {
                asset.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = ExprMenuLabel,
                    type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                    subMenu = SaveExprMenu(rigName, names, 0),
                });
            }
            EditorUtility.SetDirty(asset);
            return asset;
        }

        static VRCExpressionsMenu SaveExprMenu(string rigName, string[] names, int page)
        {
            var asset = LoadOrCreate<VRCExpressionsMenu>($"{RigBuilder.OutputFolder(rigName)}/{rigName}_Menu_Expr{page + 1}.asset");
            var start = page * (MenuSize - 1);
            var rest = names.Length - start;
            var count = rest > MenuSize ? MenuSize - 1 : rest;
            asset.controls = Enumerable.Range(start, count).Select(i => new VRCExpressionsMenu.Control
            {
                name = names[i],
                type = VRCExpressionsMenu.Control.ControlType.Toggle,
                parameter = new VRCExpressionsMenu.Control.Parameter { name = ParamsFile.ExprFlag },
                value = i + 1,
            }).ToList();
            if (rest > MenuSize)
            {
                asset.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = NextPageLabel,
                    type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                    subMenu = SaveExprMenu(rigName, names, page + 1),
                });
            }
            EditorUtility.SetDirty(asset);
            return asset;
        }

        /// <summary>生成時の layers.json の写しにある表情の名前(番号 1 から)。</summary>
        static string[] ExpressionNames(string rigName)
        {
            var layers = LayersImporter.LoadDoc(RigBuilder.ToFullPath($"{RigBuilder.OutputFolder(rigName)}/layers.json"),
                new List<string>(), out _);
            return layers == null ? new string[0] : Expression.Names(layers).Take(Expression.MaxExpressions).ToArray();
        }

        /// <summary>既存のアセットは中身だけ差し替える(GUID を保つ)。</summary>
        static T LoadOrCreate<T>(string path) where T : ScriptableObject
        {
            var asset = AssetDatabase.LoadAssetAtPath<T>(path);
            if (asset != null) return asset;
            asset = ScriptableObject.CreateInstance<T>();
            AssetDatabase.CreateAsset(asset, path);
            return asset;
        }

        /// <summary>
        /// FX レイヤーに controller を設定する。レイヤー配列は SDK の Descriptor エディタが初期化するので、
        /// 未初期化ならエディタを一度作って初期化させる。
        /// </summary>
        static bool SetFxLayer(VRCAvatarDescriptor descriptor, AnimatorController controller)
        {
            if (FxIndex(descriptor) < 0)
            {
                var editor = global::UnityEditor.Editor.CreateEditor(descriptor);
                Object.DestroyImmediate(editor);
            }
            var index = FxIndex(descriptor);
            if (index < 0) return false;

            descriptor.customizeAnimationLayers = true;
            var layers = descriptor.baseAnimationLayers;
            layers[index].isEnabled = true;
            layers[index].isDefault = false;
            layers[index].animatorController = controller;
            descriptor.baseAnimationLayers = layers;
            return true;
        }

        static int FxIndex(VRCAvatarDescriptor descriptor)
        {
            var layers = descriptor.baseAnimationLayers;
            if (layers == null) return -1;
            for (var i = 0; i < layers.Length; i++)
            {
                if (layers[i].type == VRCAvatarDescriptor.AnimLayerType.FX) return i;
            }
            return -1;
        }

        /// <summary>両目(白目)の中心の少し手前。白目が無ければ頭ルートの少し上。</summary>
        static Vector3 ViewPosition(GameObject root, List<string> warnings)
        {
            var transforms = root.GetComponentsInChildren<Transform>(true);
            var eyes = transforms.Where(t => t.name.EndsWith(RigBuilder.FunctionSuffix) &&
                                             (t.name.Contains("eye_l_white") || t.name.Contains("eye_r_white"))).ToArray();
            Vector3 world;
            if (eyes.Length > 0)
            {
                world = eyes.Aggregate(Vector3.zero, (sum, t) => sum + t.position) / eyes.Length;
            }
            else
            {
                var head = transforms.FirstOrDefault(t => t.name == RigBuilder.HeadRootName);
                world = head != null ? head.position + Vector3.up * 0.1f : root.transform.position + Vector3.up * 1.2f;
                warnings.Add("白目のレイヤーが見つからないため、視点位置を推定しました。Descriptor の View Position を確認してください");
            }
            var local = root.transform.InverseTransformPoint(world);
            return new Vector3(0f, local.y, local.z + ViewForward);
        }

        /// <summary>FX が参照するパラメータのうち、Expression Parameters に無い同期対象を警告する。</summary>
        static IEnumerable<string> CheckControllerParams(AnimatorController controller, VRCExpressionParameters parameters)
        {
            var names = new HashSet<string>(parameters.parameters.Select(p => p.name));
            return controller.parameters
                .Where(p => p.name.StartsWith("FR_") && !p.name.EndsWith("_s") &&
                            !AnimatorGenerator.LocalParams.Contains(p.name) && !names.Contains(p.name))
                .Select(p => $"{p.name}: FX にあるが Expression Parameters に無いため同期されません(params.json とアニメータの生成元が違う可能性)");
        }
    }
}
#endif
