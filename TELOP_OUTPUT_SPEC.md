# Telop Output Specification

作成日: 2026-06-14

この仕様書は、逆ジオコーディングで得た地名を、別コンテナでテロップ映像として出力する機能の設計をまとめたものです。

## 1. 目的

`get_heri_gps` と `reverse_geocoder` により得られた現在地名を、映像テロップとして外部出力します。

出力は以下の2系統です。

```text
V信号   : テロップ文字を含む映像
キー信号: テロップ部分の抜き用アルファ/マット
```

V信号とキー信号は、それぞれ別々の出力インターフェースをUIから選択できます。
どちらかを未選択にすることで、Vのみ、またはキーのみの出力も可能にします。

## 2. 全体構成

最終構成:

```text
get_heri_gps
  SDI音声からGPSを復調
  ↓ HTTP POST

reverse_geocoder
  緯度経度から都道府県・市区町村を判定
  ↓ API / WebSocket / polling

telop_output
  地名テキストを取得
  V信号とキー信号を生成
  選択された映像出力インターフェースへ出力
```

UIは既存の8010アプリ上に追加します。

```text
http://<host>:8010
```

8010 UIから `telop_output` コンテナへ設定を送ります。

## 3. コンテナ構成

`docker-compose.yml` には将来的に以下を追加します。

```yaml
services:
  telop-output:
    build: ./telop_output
    container_name: telop_output
    ports:
      - "8030:8030"
    environment:
      HOST: "0.0.0.0"
      PORT: "8030"
      REVERSE_GEOCODER_URL: "http://reverse-geocoder:8020/api/latest"
    devices:
      # DeckLink / AJA / v4l2 など、実際の出力方式に応じて指定
      # - "/dev/blackmagic:/dev/blackmagic"
      # - "/dev/video0:/dev/video0"
    volumes:
      - "./telop_output/config:/app/config"
      - "./telop_output/assets:/app/assets"
```

## 4. 入力データ

`telop_output` は地名データを `reverse_geocoder` から取得します。

基本入力:

```text
GET http://reverse-geocoder:8020/api/latest
```

取得例:

```json
{
  "ok": true,
  "time": "2026-06-13T13:24:04+09:00",
  "lat": 34.591708,
  "lon": 135.574025,
  "prefecture": "大阪府",
  "city": "松原市",
  "address_label": "大阪府松原市"
}
```

表示テキストの初期値:

```text
address_label
```

例:

```text
大阪府松原市
```

## 5. 出力信号

### 5.1 V信号

V信号は、テロップ文字を描画した映像です。

初期仕様:

```text
解像度: 1920x1080
フレームレート: 59.94i または 29.97p/59.94p
背景: 黒または透明相当の黒
文字: UI設定に従う
```

最初の実装では、Docker内の生成はRGBAフレームとして扱い、出力先に応じて変換します。

### 5.2 キー信号

キー信号は、テロップ部分の抜き用マットです。

基本:

```text
文字/装飾がある部分: 白
透明部分: 黒
アンチエイリアス部分: グレー
```

V信号の位置、サイズ、フォント、装飾に完全追従します。
ユーザーがキー側を個別に配置する必要はありません。

### 5.3 Vのみ/キーのみ

UIで出力インターフェースを未選択にできます。

```text
V出力: 未選択
キー出力: DeckLink SDI 1
=> キーのみ出力

V出力: DeckLink SDI 1
キー出力: 未選択
=> Vのみ出力
```

両方未選択の場合は、プレビューのみ動作します。

## 6. 出力インターフェース選択

V信号とキー信号は、それぞれプルダウンで選択します。

UI:

```text
V出力インターフェース   [ 未選択 / DeckLink ... / AJA ... / v4l2 ... ]
キー出力インターフェース [ 未選択 / DeckLink ... / AJA ... / v4l2 ... ]
```

API:

```text
GET /api/output-devices
```

レスポンス例:

```json
{
  "devices": [
    {
      "id": "",
      "label": "未選択",
      "kind": "none"
    },
    {
      "id": "decklink:0",
      "label": "DeckLink SDI 1",
      "kind": "decklink"
    },
    {
      "id": "v4l2:/dev/video0",
      "label": "v4l2 /dev/video0",
      "kind": "v4l2"
    }
  ]
}
```

実際の出力方式は、接続機材に応じて以下のいずれかを採用します。

```text
DeckLink SDK / ffmpeg decklink
AJA SDK / ffmpeg対応デバイス
v4l2 loopback
NDI/SRT等のネットワーク映像
```

まずは出力インターフェース検出を抽象化し、実機に合わせて実装差し替えできる構造にします。

## 7. テロップ設定

UIから以下を変更できます。

### 7.1 テキスト

```text
表示形式:
  address_label
  prefecture + city
  任意固定文言 + address_label
```

例:

```text
現在地: 大阪府松原市
```

### 7.2 フォント

UIから柔軟に変更できます。

設定項目:

```text
フォントファミリー
太さ
サイズ
文字色
縁取り色
縁取り幅
背景色
背景透明度
字間
行間
```

フォントはコンテナ内の以下から読み込みます。

```text
telop_output/assets/fonts/
```

将来的にはUIからフォントファイルをアップロード可能にします。

### 7.3 配置と拡大縮小

V信号上のテロップ位置・サイズは、8010 UIのプレビュー上でドラッグ操作により決定します。

操作:

```text
ドラッグ: 位置移動
四隅ハンドル: 拡大縮小
数値入力: x, y, width, height, scale
```

設定値:

```json
{
  "x": 120,
  "y": 820,
  "width": 900,
  "height": 120,
  "scale": 1.0
}
```

座標系:

```text
基準解像度: 1920x1080
左上原点
x/y/width/height はピクセル
```

出力解像度が変わる場合は、基準解像度からスケール変換します。

## 8. UI仕様

8010 UIに `テロップ出力` セクションを追加します。

表示項目:

```text
現在の地名
V出力インターフェース
キー出力インターフェース
出力開始/停止
フォント設定
色設定
プレビュー
ドラッグ可能なテロップボックス
```

UIは以下のAPIを使って `telop_output` と通信します。

```text
GET  /api/telop/status
GET  /api/telop/output-devices
GET  /api/telop/config
POST /api/telop/config
POST /api/telop/start
POST /api/telop/stop
```

8010アプリ側は、これらを `telop_output` へプロキシするか、フロントエンドから直接 `8030` へアクセスします。

推奨:

```text
8010側でプロキシする
```

理由:

```text
ブラウザは8010だけ開けばよい
CORSを気にしなくてよい
Docker内部名をブラウザに見せない
```

## 9. telop_output API仕様

### 9.1 GET /api/status

状態を返します。

```json
{
  "ok": true,
  "running": true,
  "latest_text": "大阪府松原市",
  "v_output": "decklink:0",
  "key_output": "decklink:1"
}
```

### 9.2 GET /api/output-devices

利用可能な出力インターフェース一覧を返します。

### 9.3 GET /api/config

現在のテロップ設定を返します。

### 9.4 POST /api/config

テロップ設定を更新します。

リクエスト例:

```json
{
  "v_output": "decklink:0",
  "key_output": "decklink:1",
  "font_family": "Noto Sans CJK JP",
  "font_size": 72,
  "font_weight": 700,
  "text_color": "#ffffff",
  "stroke_color": "#000000",
  "stroke_width": 6,
  "box": {
    "x": 120,
    "y": 820,
    "width": 900,
    "height": 120,
    "scale": 1.0
  }
}
```

### 9.5 POST /api/start

V/キー出力を開始します。

### 9.6 POST /api/stop

V/キー出力を停止します。

## 10. 描画処理

描画エンジン候補:

```text
Python + Pillow
Python + Cairo/Pango
Node.js + canvas
ffmpeg drawtext/filter_complex
```

日本語フォントや縁取り品質を考えると、初期実装は以下がよいです。

```text
Python + Pillow + Noto Sans CJK
```

処理:

```text
1. 最新地名を取得
2. RGBAキャンバスへ文字を描画
3. Vフレームを生成
4. Aチャンネルからキー信号を生成
5. V出力先へ送る
6. キー出力先へ送る
```

## 11. 出力実装方針

最初の実装では、出力デバイス依存を小さくするため、出力バックエンドを分けます。

```text
PreviewBackend
  画像生成のみ。実機なしでテスト可能。

FfmpegBackend
  ffmpegへrawvideoをpipeし、指定デバイスへ出力。

DeckLinkBackend
  必要ならDeckLink専用設定を追加。
```

初期段階では以下を目標にします。

```text
1. プレビュー画像生成
2. V画像PNG / キー画像PNGをAPIで確認
3. ffmpeg rawvideo出力に接続
4. 実機V/キー出力
```

## 12. プレビュー

`telop_output` は、現在のV/キー画像を確認するAPIを持ちます。

```text
GET /api/preview/v.png
GET /api/preview/key.png
```

8010 UIのプレビューは `v.png` を表示し、その上でドラッグ操作を行います。
キーは同じ設定から自動生成されるため、UI上で別操作は不要です。

## 13. 障害時の扱い

### reverse_geocoderが未接続

最後に取得できた地名を継続表示します。
地名が一度も取得できていない場合は空欄または `-` を表示します。

### V/キー出力デバイスが未選択

未選択の出力は行いません。
両方未選択の場合はプレビューのみです。

### V/キー出力デバイスが失敗

対象出力だけエラーにし、もう片方が正常なら継続します。

例:

```text
V出力失敗、キー出力成功
=> キーのみ継続、UIにV出力エラー表示
```

## 14. 設定保存

設定は以下に保存します。

```text
telop_output/config/telop_config.json
```

コンテナ再起動後も同じ設定で復帰します。

## 15. 今後の実装順

推奨順:

```text
1. telop_output コンテナ雛形作成
2. reverse_geocoder /api/latest から地名取得
3. PillowでV/キーPNGプレビュー生成
4. 8010 UIにテロップ設定セクション追加
5. ドラッグで配置・拡大縮小できるプレビューUI実装
6. 出力インターフェース一覧API実装
7. ffmpeg出力バックエンド実装
8. 実機V/キー出力テスト
```

## 16. 未確定事項

実装前に確認が必要な項目:

```text
V/キーの実出力デバイス種別
出力解像度とフレームレート
インターレース要否
V信号の背景仕様
キー信号の白黒レンジ
テロップのセーフエリア
使用フォントのライセンス
```

このため、最初はプレビューとAPIまでを機材非依存で作り、その後に実出力バックエンドを接続します。
