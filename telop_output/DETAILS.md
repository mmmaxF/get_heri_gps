# telop_output 詳細設計

`telop_output` は、逆ジオコーダーが保持している最新地名を取得し、V信号用プレビュー画像とKey信号用プレビュー画像を生成するコンテナです。現在の実装はプレビュー生成と設定保存が中心で、実SDIハードウェアへ直接出力するバックエンドは未実装です。

## 役割

```text
reverse_geocoderから最新地名を取得
  ↓
UI設定に従って表示文字列を作る
  ↓
フォント、色、マット、位置、サイズを反映してRGBA画像を描画
  ↓
VプレビューPNGを返す
  ↓
KeyプレビューPNGを返す
```

`gps_receiver` の8010画面は、このコンテナのAPIをプロキシして、テロップ設定とプレビューを同じ画面で操作できるようにしています。

## 主なファイル

| ファイル | 役割 |
|---|---|
| `app.py` | FastAPI、設定保存、フォント一覧、地名取得、V/Key画像生成 |
| `assets/fonts/` | 追加フォント置き場 |
| `.env` | 待受ポート、設定ファイル、逆ジオコーダーURL、ログ設定 |
| `README.md` | 簡易説明 |

## 入力と出力

### 入力

1. `reverse_geocoder` の最新地名API

```text
GET http://reverse-geocoder:8020/api/latest
```

2. UIから保存されるテロップ設定

```text
V出力
Key出力
映像フォーマット
テキストテンプレート
フォント
文字揃え
文字色
縁取り
Vマット色・濃度
Keyマット濃度
表示位置・サイズ
```

### 出力

| API | 出力 |
|---|---|
| `/api/preview/v.png` | V信号用のRGB PNG |
| `/api/preview/key.png` | Key信号用の白黒PNG |

V/Key出力デバイスが未選択でも、プレビュー画像は生成できます。

## 設定値

主な設定は `telop_output/.env` に置きます。

| 変数 | 意味 |
|---|---|
| `HOST` / `PORT` | APIの待受アドレスとポート |
| `TELOP_CONFIG_PATH` | UI設定を保存するJSONファイル |
| `REVERSE_GEOCODER_LATEST_URL` | 最新地名を読むAPI |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | ログローテーション設定 |

## アプリ内設定

`app.py` の `DEFAULT_CONFIG` が初期値です。UIから変更すると `TELOP_CONFIG_PATH` にJSONで保存され、次回起動時に読み込まれます。

主な設定項目:

| 項目 | 内容 |
|---|---|
| `v_output` | V信号の出力先。未選択可 |
| `key_output` | Key信号の出力先。未選択可 |
| `format.width` / `format.height` | 出力解像度 |
| `format.frame_rate` | フレームレート表記 |
| `format.pixel_format` | ピクセル形式表記 |
| `format.key_mode` | Key形式 |
| `text_template` | 地名から表示文字を作るテンプレート |
| `fallback_text` | 地名未取得時の表示文字 |
| `font_family` | 使用フォント |
| `font_size` | 基準フォントサイズ |
| `text_align` | `left` / `center` / `right` |
| `text_color` | 文字色 |
| `stroke_color` / `stroke_width` | 縁取り色と太さ |
| `background_color` | Vマット色 |
| `background_opacity` | Vマット濃度 |
| `key_background_opacity` | Keyマット濃度 |
| `padding` | テキスト枠内の余白 |
| `box.x` / `box.y` | 表示位置 |
| `box.width` / `box.height` | 表示枠サイズ |
| `box.scale` | 表示倍率 |

## 起動時の流れ

1. `setup_logger()` がローテーション付きログを作る。
2. `State.load_config()` が保存済みの `telop_config.json` を読む。
3. 保存済み設定があれば `DEFAULT_CONFIG` に上書きする。
4. FastAPIがテロップ設定APIとプレビューAPIを公開する。

保存済みJSONの読み込みに失敗した場合は、初期値で起動します。

## 地名取得

`get_latest_geocode()` が `REVERSE_GEOCODER_LATEST_URL` を読みます。

```text
GET /api/latest
  ↓
ok == true なら地名データとして使う
  ↓
ok != true または通信失敗なら None を返す
```

通信失敗時もプレビュー生成は止まりません。その場合は `fallback_text` を表示します。

## 表示文字列の作り方

`render_text(config, geocode)` が表示文字列を作ります。

`text_template` では次の値を使えます。

| 変数 | 内容 |
|---|---|
| `{address_label}` | 都道府県+市区町村 |
| `{prefecture}` | 都道府県 |
| `{city}` | 市区町村 |
| `{ward}` | 区 |

例:

```text
{address_label}
{prefecture} {city}
現在地: {address_label}
```

テンプレートの結果が空、または地名が未取得の場合は `fallback_text` が使われます。

## フォント選択

`available_fonts()` がフォント一覧を作ります。

探索先:

```text
/app/assets/fonts
/usr/share/fonts
```

`assets/fonts/` に `.ttf`、`.otf`、`.ttc` を置くと、UIのフォントプルダウンに出ます。システムフォントは、日本語表示に向いたNoto CJK、IPA/IPAex、Takao、M+などを優先し、DejaVu系など日本語が四角になりやすいものは除外しています。

`find_font(font_family)` は、設定されたフォント名またはパスに一致するフォントを探します。見つからない場合は、Noto系、次に最初の候補を使います。

## V画像生成

`preview_v()` が `/api/preview/v.png` の入口です。

内部では `render_rgba()` で透明付きRGBA画像を作り、黒背景に合成してRGB PNGとして返します。

```text
現在設定を取得
  ↓
render_rgba()
  ↓
黒背景とalpha composite
  ↓
PNGとして返す
```

V画像には、文字、縁取り、Vマット色、Vマット濃度が反映されます。UI上の配置用ガイド枠はブラウザ側の操作補助であり、生成PNGには入りません。

## Key画像生成

`preview_key()` が `/api/preview/key.png` の入口です。

内部では `render_rgba()` でRGBA画像を作り、そのアルファチャンネルだけを取り出して白黒画像にします。

```text
RGBA画像を生成
  ↓
alphaチャンネルを取り出す
  ↓
R/G/Bすべてにalphaを入れる
  ↓
白黒PNGとして返す
```

Keyでは、文字やマットがある部分ほど白く、透明な部分ほど黒くなります。`key_background_opacity` を `0` にすると、文字以外のマット部分は黒になります。

## 描画処理

`render_rgba(config, key_background_opacity=None)` が描画の中心です。

処理順:

1. 出力解像度で透明RGBA画像を作る。
2. `get_latest_geocode()` で最新地名を読む。
3. `render_text()` で表示文字列を作る。
4. `STATE.latest_text` と `STATE.latest_geocode` を更新する。
5. `box` から位置とサイズを決める。
6. `background_color` と濃度でマットを描く。
7. `find_font()` でフォントファイルを決める。
8. `fit_font()` で枠内に収まるフォントサイズへ調整する。
9. `text_align` に従って左寄せ、中央、右寄せ位置を決める。
10. `draw.text()` で文字と縁取りを描く。

## 文字サイズの自動調整

`fit_font()` は、指定フォントサイズから2pxずつ下げながら、テキストが枠内に収まるサイズを探します。

これにより、地名が長い場合でもボックスから大きくはみ出しにくくしています。

## 出力デバイス一覧

`output_devices()` は `/dev/video*` のうち書き込み可能なものを `v4l2:` デバイスとして返します。

ただし、現在の実装ではデバイス一覧表示までで、実際の映像フレーム送出処理はまだありません。HDMI/Display出力の候補は `gps_receiver` 側で `/sys/class/drm` を読んで補完しています。

## API

| API | 内容 |
|---|---|
| `GET /api/status` | 実行状態、最新テキスト、最新地名、現在設定 |
| `GET /api/output-devices` | V/Key出力候補 |
| `GET /api/fonts` | UIに出すフォント一覧 |
| `GET /api/config` | 現在のテロップ設定 |
| `POST /api/config` | テロップ設定を保存 |
| `POST /api/start` | テロップ出力状態を開始にする |
| `POST /api/stop` | テロップ出力状態を停止にする |
| `GET /api/preview/v.png` | VプレビューPNG |
| `GET /api/preview/key.png` | KeyプレビューPNG |

## ログ

ログはローテーションされ、コンテナ内では次に出ます。

```text
/app/logs/telop_output.log
```

ホスト側では次です。

```text
telop_output/logs/telop_output.log
```

主なログ:

| `flow=` | 意味 |
|---|---|
| `telop init` | 起動時設定 |
| `telop config_saved` | 設定JSON保存 |
| `telop config_update` | UIから設定更新 |
| `telop text_update` | 表示文字列が変化 |
| `telop start` | 開始状態へ変更 |
| `telop stop` | 停止状態へ変更 |

## 別サーバ化するときの注意

`telop_output` を別サーバへ移す場合、最新地名を読むURLを変更します。

```text
telop_output/.env
  REVERSE_GEOCODER_LATEST_URL=http://<reverse_geocoderサーバ>:8020/api/latest
```

`gps_receiver` のUIからテロップ設定を操作したい場合は、`gps_receiver/.env` も変更します。

```text
gps_receiver/.env
  TELOP_OUTPUT_URL=http://<telop_outputサーバ>:8030
```

現時点ではV/Keyの実出力処理は未実装です。将来追加する場合は、`render_rgba()` が返すRGBA画像をフレーム単位でSDI/DeckLink/AJA/v4l2等へ送る層を追加します。

