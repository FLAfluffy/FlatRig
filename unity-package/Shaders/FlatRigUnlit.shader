// 最小の Unlit 透過シェーダ(docs/SPEC.md §5.6)。PC 専用。
// 描画順はメッシュの三角形の並び(order 順)で決まるため ZWrite Off。
// まばたき: 白目・黒目の頂点(UV2.x = 1 左目 / 2 右目)は、白目の形を _FR_LidL/R(px)だけ下にずらした範囲の外を消す
// (まつ毛より上をまぶたで隠す表現。docs/SPEC.md §5.4。UV1〜3 は RigBuilder が入れる)。
// 白目の影(UV2.w = 1)はボーンで _FR_LashL/R(px)だけ下がるので、その分を戻した位置の白目で判定し、白目の外も消す。
// 瞳のハイライト(UV2.w = 2)はまぶたに押されて _FR_HiL/R(px)だけ下がるので、その分を戻した位置で判定する。
// 表情と絵の差し替え(docs/SPEC.md §5.9、Expression.cs): アルファに次を掛ける。
//   表情: UV4.x のビット(bit k = 表情 k で表示)と _FR_ExprA〜D(表情 0〜15 の重み。切り替えの間は 2 つの表情が混ざる)の積の和
//   口の絵: UV4.y(この絵になる開き具合)から _FR_MouthOpen が離れるほど薄く、UV4.z(隣の絵の基準までの幅)だけ離れると 0
//          (隣の絵と連続して混ざる。半分ずつのときも抜けて見えにくいよう 1 − (1 − t)² で重ねる。大きさ合わせはボーン。
//          AnimatorGenerator)。UV4.z ≥ 50 は口の絵でない部品で、常に 1
//   目の絵: UV4.w = 1/3 は左/右の目が開いているとき、2/4 は閉じているとき(_FR_EyeOpenL/R < _FR_EyeClosedAt)に 1
Shader "FlatRig/Unlit"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Color", Color) = (1, 1, 1, 1)
        _FR_LidL ("Eye Lid L (px)", Float) = 0
        _FR_LidR ("Eye Lid R (px)", Float) = 0
        _FR_LashL ("Eye Lash Drop L (px)", Float) = 0
        _FR_LashR ("Eye Lash Drop R (px)", Float) = 0
        _FR_HiL ("Eye Highlight Push L (px)", Float) = 0
        _FR_HiR ("Eye Highlight Push R (px)", Float) = 0
        _FR_ExprA ("Expression Weights 0-3", Vector) = (1, 0, 0, 0)
        _FR_ExprB ("Expression Weights 4-7", Vector) = (0, 0, 0, 0)
        _FR_ExprC ("Expression Weights 8-11", Vector) = (0, 0, 0, 0)
        _FR_ExprD ("Expression Weights 12-15", Vector) = (0, 0, 0, 0)
        _FR_MouthOpen ("Mouth Open (0-1)", Float) = 0
        _FR_EyeOpenL ("Eye Open L (0-1)", Float) = 1
        _FR_EyeOpenR ("Eye Open R (0-1)", Float) = 1
        _FR_EyeClosedAt ("Eye Closed Picture Below", Float) = 0.12
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" "VRCFallback"="UnlitTransparent" }
        LOD 100

        Pass
        {
            Blend SrcAlpha OneMinusSrcAlpha
            ZWrite Off
            Cull Off

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                float2 lidUv : TEXCOORD1;    // 同じ位置の白目の UV
                float4 lidInfo : TEXCOORD2;  // (目, 1px あたりの UV の高さ, 白目の UV 上端, 白目の影なら 1)
                float4 lidRect : TEXCOORD3;  // 白目の UV 左端・右端・下端
                float4 expr : TEXCOORD4;     // x: 表示する表情のビット、yz: 口の開き具合の範囲、w: 目の絵の差し替え(Expression.cs)
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float4 lid : TEXCOORD1;      // xy: 調べる白目の UV、z: 白目の UV 上端、w: 切り取るか(0/1)
                float4 lidRect : TEXCOORD2;
                float3 inWhite : TEXCOORD3;  // xy: 今の位置の白目の UV(白目の影だけ)、z: 白目の外を消すか(0/1)
                float exprAlpha : TEXCOORD4; // 表情・絵の差し替えによるアルファの倍率
                UNITY_VERTEX_OUTPUT_STEREO
            };

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _Color;
            float _FR_LidL;
            float _FR_LidR;
            float _FR_LashL;
            float _FR_LashR;
            float _FR_HiL;
            float _FR_HiR;
            float4 _FR_ExprA;
            float4 _FR_ExprB;
            float4 _FR_ExprC;
            float4 _FR_ExprD;
            float _FR_MouthOpen;
            float _FR_EyeOpenL;
            float _FR_EyeOpenR;
            float _FR_EyeClosedAt;

            // UV4 から、表情・口の絵・目の絵によるアルファの倍率を求める
            float ExprAlpha(float4 expr)
            {
                float bits = floor(expr.x + 0.5);
                float4 b0 = fmod(floor(bits / float4(1, 2, 4, 8)), 2);
                float4 b1 = fmod(floor(bits / float4(16, 32, 64, 128)), 2);
                float4 b2 = fmod(floor(bits / float4(256, 512, 1024, 2048)), 2);
                float4 b3 = fmod(floor(bits / float4(4096, 8192, 16384, 32768)), 2);
                float a = saturate(dot(b0, _FR_ExprA) + dot(b1, _FR_ExprB) + dot(b2, _FR_ExprC) + dot(b3, _FR_ExprD));
                float near = saturate(1 - abs(_FR_MouthOpen - expr.y) / max(expr.z, 1e-4));
                a *= expr.z >= 50 ? 1 : 1 - (1 - near) * (1 - near);
                float code = expr.w;
                float open = step(_FR_EyeClosedAt, code < 2.5 ? _FR_EyeOpenL : _FR_EyeOpenR);
                float eye = code < 0.5 ? 1 : (fmod(code + 0.5, 2) < 1 ? 1 - open : open);  // 2/4: 閉じた目の絵、1/3: 通常の目
                return a * eye;
            }

            v2f vert (appdata v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_OUTPUT(v2f, o);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                float eye = v.lidInfo.x;
                float shift = eye < 0.5 ? 0 : (eye < 1.5 ? _FR_LidL : _FR_LidR);
                float kind = eye > 0.5 ? v.lidInfo.w : 0;
                float shadow = (kind > 0.5 && kind < 1.5) ? 1 : 0;
                float highlight = kind > 1.5 ? 1 : 0;
                float drop = shadow * (eye < 1.5 ? _FR_LashL : _FR_LashR) + highlight * (eye < 1.5 ? _FR_HiL : _FR_HiR);
                // キャンバスの下向き = UV の下向き(y が減る)。下がった分(drop)を戻した位置で判定する
                o.lid = float4(v.lidUv + float2(0, (shift - drop) * v.lidInfo.y), v.lidInfo.z, (eye > 0.5 && shift > 0.001) ? 1 : 0);
                o.lidRect = v.lidRect;
                o.inWhite = float3(v.lidUv - float2(0, drop * v.lidInfo.y), shadow);
                o.exprAlpha = ExprAlpha(v.expr);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                fixed4 col = tex2D(_MainTex, i.uv) * _Color;
                float inside = step(i.lidRect.x, i.lid.x) * step(i.lid.x, i.lidRect.y) * step(i.lid.y, i.lid.z);
                float lid = smoothstep(0.3, 0.7, tex2D(_MainTex, i.lid.xy).a * inside);
                col.a *= lerp(1, lid, i.lid.w);
                float insideNow = step(i.lidRect.x, i.inWhite.x) * step(i.inWhite.x, i.lidRect.y) * step(i.inWhite.y, i.lid.z) * step(i.lidRect.z, i.inWhite.y);
                float white = smoothstep(0.3, 0.7, tex2D(_MainTex, i.inWhite.xy).a * insideNow);
                col.a *= lerp(1, white, i.inWhite.z);
                col.a *= i.exprAlpha;
                return col;
            }
            ENDCG
        }
    }
    Fallback Off
}
