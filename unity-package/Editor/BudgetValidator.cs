using System.Linq;
#if FLATRIG_VRCSDK
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDKBase.Editor.BuildPipeline;
#endif

namespace FlatRig.Editor
{
    /// <summary>同期ビット予算(docs/SPEC.md §3.3)の計算と、アップロード前の検査。</summary>
    public static class BudgetValidator
    {
        public const int MaxBits = 256;

        /// <summary>params.json の同期パラメータの合計ビット(float 8bit、Bool 1bit。フラグは常に含める)。</summary>
        public static int SyncedBits(ParamsDoc doc) =>
            doc.@params.Where(p => p.synced).Sum(p => p.bits) + (doc.flags ?? new FlagEntry[0]).Where(f => f.synced).Sum(f => f.bits);
    }

#if FLATRIG_VRCSDK
    /// <summary>
    /// VRChat SDK のビルド前に呼ばれ、Expression Parameters の合計コストが上限を超えていたらビルドを止める。
    /// FlatRig のパラメータに加え、利用者が足した他のパラメータも含めて数える。
    /// </summary>
    public class BudgetPreprocess : IVRCSDKPreprocessAvatarCallback
    {
        public int callbackOrder => -100;

        public bool OnPreprocessAvatar(GameObject avatarGameObject)
        {
            var descriptor = avatarGameObject.GetComponent<VRCAvatarDescriptor>();
            if (descriptor == null || descriptor.expressionParameters == null) return true;

            var cost = descriptor.expressionParameters.CalcTotalCost();
            if (cost <= BudgetValidator.MaxBits) return true;
            Debug.LogError($"[FlatRig] 同期パラメータが {cost} bit で、上限 {BudgetValidator.MaxBits} bit を超えています。ビルドを中止しました");
            return false;
        }
    }
#endif
}
