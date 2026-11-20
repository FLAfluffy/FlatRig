FlatRig
=======

パーツ分けした立ち絵の PSD から VRChat 用の平面アバターを作り、VTube Studio の顔の動きで動かすためのツール一式です。

・FlatRigPSD.exe: PSD を部品に分けて、アバター用の素材を書き出す
・FlatRigTools.unitypackage: 書き出した素材から、Unity でアバターを作る
・FlatRig Bridge(FlatRigBridge.exe): VTube Studio の顔の動き(数値)を、VRChat のアバターへ OSC で送る
  通信は同じ PC の中だけ(127.0.0.1)で行います。

アバターの作り方は、同じフォルダの「アバター作成ガイド.html」をブラウザで開いて見てください。
ここから下は、主に FlatRig Bridge の使い方です。

■ 使い方(FlatRig Bridge)
1. VRChat を起動し、Action Menu → Options → OSC → Enabled にする。
   FlatRig で作ったアバターに着替える。
2. VTube Studio を起動し、モデルを読み込んでトラッキングを始める。
   設定の「APIの起動(プラグインを許可)」を ON にする。
3. FlatRigBridge.exe をダブルクリックする。
   初回は VTube Studio に許可の画面が出るので「Allow」を押す。
4. ウィンドウに「中継中」と出たら、VRChat の Expression Menu で FlatRig を ON にする。
5. やめるときは [終了] ボタンを押す。

× でウィンドウを閉じると、タスクトレイ(画面右下)に隠れて動き続けます。
アイコンをクリックすると再表示、右クリックで「終了」できます。

■ VRChat との連動(ウィンドウのチェック項目)
・VRChat を閉じたら終了する(既定: ON)
    VRChat の終了を検知すると、このアプリも終了します。
・Windows の起動時に待機する(既定: OFF)
    サインイン時にタスクトレイで待機し、VRChat が動いている間だけ中継します。
・VRChat と同時起動するショートカットを作成
    デスクトップに「VRChat (FlatRig 連動)」を作ります。これを開くと、
    Steam 版の VRChat とこのアプリが一緒に起動します。

■ フォルダの中身
FlatRigBridge.exe          ウィンドウ版(普段はこちら)
flatrig-cli.exe            コマンド版(設定の確認や VTS のパラメータ一覧の取得)
FlatRigPSD.exe             PSD をドロップすると確認画面が開き、部品分けを確かめてからアバター用のレイヤー素材を書き出す
FlatRigTools.unitypackage  Unity 用のツール(Unity の Project ウィンドウにドラッグ&ドロップして入れる)
アバター作成ガイド.html    自分の PSD からアバターを作る手順
config\settings.example.json   設定の見本
config\params.example.json     パラメータの対応表の見本
LICENSE.txt                FlatRig のライセンス(MIT License)
THIRD_PARTY_LICENSES.txt   同梱している他のソフトウェアのライセンス

設定を変える場合は、見本をコピーして settings.json(または params.json)という名前で
同じフォルダに置いてください。見本より優先して読み込みます。
初回の許可で config\.vts_token.json が作られます。これは認証用の情報なので、
他人に渡したり公開したりしないでください。
動作の記録は logs\bridge.log に残ります。
FlatRigPSD.exe の記録は %LOCALAPPDATA%\FlatRig\FlatRigPSD.log に残ります。

■ うまく動かないとき
・「VTube Studio に接続中…」のまま: VTube Studio の API が ON か、ポートが 8001 か確認。
・「VTube Studio の許可を待っています」のまま: VTube Studio の許可画面で「Allow」を押す。
  許可を取り消した場合は、アプリを終了して config\.vts_token.json を削除してから起動し直す。
・「VTube Studio のモデル待ち」のまま: VTube Studio でモデルを読み込む。
・アバターが動かない: VRChat の OSC が Enabled か、Expression Menu の FlatRig が ON か確認。
  アップロード済みのアバターでパラメータを変えた場合は、OSC → Reset Config を押す。
・設定の確認: このフォルダで  flatrig-cli.exe check-config

■ アンインストール
「Windows の起動時に待機する」を OFF にしてから終了し、このフォルダを削除してください。
(ON のまま削除すると、Windows のスタートアップ登録が残ります)

■ ライセンス
FlatRig は MIT License で公開しています(LICENSE.txt)。
改変・再配布・商用利用ができます。再配布するときは、著作権表示と LICENSE.txt を一緒に入れてください。
本ソフトウェアは無保証です。使用によって生じた損害について、作者は責任を負いません。
作ったアバターの絵の権利は、このライセンスとは別です(アバター作成ガイドの 1.2)。

■ クレジット・お問い合わせ
制作: ふら(X: @FLA_fluffy)
質問・要望・バグ報告は、X のリプライまたは DM で受け付けています。
バグ報告のときは、次の内容を添えていただけると原因を調べやすくなります。
・どの操作をしたときに、何が起きたか(画面に出たメッセージ)
・記録ファイル: logs\bridge.log(FlatRigPSD.exe の場合は %LOCALAPPDATA%\FlatRig\FlatRigPSD.log)
  記録ファイルにはフォルダのパス(PC のユーザー名を含むことがあります)が入るため、
  公開の場には貼らず DM で送ってください。
