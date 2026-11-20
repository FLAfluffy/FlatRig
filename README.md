# FlatRig

パーツ分けした立ち絵の PSD から VRChat 用の平面アバターを作り、VTube Studio の顔の動きで動かすためのツール一式です。

- **FlatRigPSD**: PSD のレイヤーを自動で部品(前髪・目・口・体など)に分け、確認画面で直してから、アバター用の素材を書き出します
- **FlatRig Editor Tools**(Unity): 書き出した素材から、メッシュ・アニメーション・VRChat の Expression Menu までを作ります
- **FlatRig Bridge**: VTube Studio のトラッキングの値を、VRChat の OSC へ送ります(通信は同じ PC の中だけ)

## ダウンロード

[Releases](../../releases) から `FlatRig-<バージョン>.zip` をダウンロードして展開してください。
使い方は、zip に入っている「アバター作成ガイド.html」(このリポジトリの [docs/AVATAR_GUIDE.md](docs/AVATAR_GUIDE.md) と同じ内容)にまとめています。

動作環境: Windows、VRChat Creator Companion で作ったアバター用 Unity プロジェクト(VRChat SDK Avatars 3.10 以降)、VTube Studio(PC 版)

## ソースから使う・ビルドする

Python 3.11 以上が必要です。

```
cd bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,tools,build]"
.\.venv\Scripts\python.exe -m pytest                 # テスト
.\.venv\Scripts\python.exe packaging\build.py        # 配布用の zip を bridge\dist に作る
```

| フォルダ | 内容 |
|---|---|
| `bridge/` | FlatRig Bridge(Python)と、配布物を作るスクリプト(`packaging/`) |
| `tools/` | FlatRigPSD の本体(`psd_to_layers.py`・`psd_editor.py`)と検査スクリプト |
| `unity-package/` | Unity 用の Editor 拡張とシェーダ |
| `docs/` | 利用者向けのガイド |

## ライセンス

[MIT License](LICENSE) です。改変・再配布・商用利用ができます。再配布するときは、著作権表示とライセンス文を一緒に入れてください。

作ったアバターの絵の権利は、このライセンスとは別です。使う絵の権利は、ガイドの「1.2 絵の権利について」で確認してください。

## クレジット・お問い合わせ

制作: ふら(X: [@FLA_fluffy](https://x.com/FLA_fluffy))

質問・要望・バグ報告は、X のリプライまたは DM で受け付けています。
