# Data Flow

このドキュメントは、GPS入り音声がCSV・地名・テロップ表示へ変換されるまでの流れを、コンテナ単位と関数単位でまとめたものです。

## 全体像

```text
SDI/HDMI/USB音声入力
  ↓
gps_receiver コンテナ
  1. ALSA/arecordでPCM音声を取得
  2. 指定音声チャンネルだけを抽出
  3. 1200/1800Hz FSKをビット列へ復調
  4. HDLC/AX.25風フレームを検証
  5. :MODペイロードから緯度・経度・高度を抽出
  6. gps_positions.csvへ保存
  7. reverse_geocoderへHTTP POST
  ↓
reverse_geocoder コンテナ
  8. 緯度・経度から都道府県・市区町村を検索
  9. geocoded_positions.csvへ保存
  10. 最新地名をAPIで保持
  ↓
telop_output コンテナ
  11. reverse_geocoderから最新地名を取得
  12. Vプレビュー/Keyプレビュー画像を生成
  ↓
gps_receiver UI
  13. 最新位置・地名・受信履歴・テロッププレビューを表示
```

## コンテナ構成

| コンテナ | フォルダ | 主な役割 | ポート |
|---|---|---|---|
| `get_heri_gps` | `gps_receiver/` | SDI/HDMI/USB音声からGPSを復調する | `8010` |
| `reverse_geocoder` | `reverse_geocoder/` | 緯度・経度を地名へ変換する | `8020` |
| `telop_output` | `telop_output/` | 地名テロップのV/Keyプレビューを生成する | `8030` |

ルートの `docker-compose.yml` は3コンテナ一括起動用です。各フォルダ内の `docker-compose.yml` は、将来コンテナを別サーバへ分離するときの単独起動用です。

コンテナごとの詳細な仕組みは以下にまとめています。

```text
gps_receiver/DETAILS.md
reverse_geocoder/DETAILS.md
telop_output/DETAILS.md
```

## 1. GPS音声入力

### コンテナ

`get_heri_gps`

### 主なファイル

`gps_receiver/app.py`

### 処理

UIで選択された入力デバイスから、`arecord` でPCM音声を読みます。

```text
例:
arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -t raw
```

### 関数

| 関数 | 役割 |
|---|---|
| `build_arecord_command(device, channels)` | 入力デバイスとチャンネル数から `arecord` コマンドを作る |
| `list_capture_devices()` | `arecord -l` を読み、UIに出す入力デバイス一覧を作る |
| `iter_command_chunks(config, stop_event)` | 実機入力から0.25秒単位でPCM音声を読み続ける |
| `iter_test_chunks(config, stop_event)` | テスト用RAWファイルからPCM音声を読む |

### 入力データ

```text
signed 16-bit little-endian PCM
sample rate: 48000Hz
channels: INPUT_CHANNELS
```

### 出力データ

指定されたGPS音声チャンネルだけを抜き出した、単一チャンネルPCM配列です。

## 2. リアルタイム処理制御

### コンテナ

`get_heri_gps`

### 主なファイル

`gps_receiver/app.py`

### 関数

| 関数 | 役割 |
|---|---|
| `start()` | `/api/start`。復調用スレッドを開始する |
| `stop()` | `/api/stop`。復調用スレッドに停止指示を出す |
| `worker_main()` | 音声取得、復調、CSV保存、逆ジオコード送信を行う中心処理 |
| `AppState.snapshot()` | UI/WebSocket/APIへ返す現在状態を作る |
| `websocket(ws)` | `/ws`。0.5秒ごとに最新状態をUIへ送る |

### 処理

`worker_main()` は以下を繰り返します。

```text
PCM音声チャンクを受け取る
  ↓
20秒分程度のバッファに保持
  ↓
1秒ごとに decode_samples() を呼ぶ
  ↓
新しいGPSレコードだけCSV・UI・逆ジオコーダーへ流す
```

## 3. GPS復調

### コンテナ

`get_heri_gps`

### 主なファイル

`gps_receiver/gps_demodulator.py`

### 関数と処理順

| 順番 | 関数 | 処理内容 |
|---|---|---|
| 1 | `normalize_samples(samples)` | PCM音声の平均値を引き、振幅を正規化する |
| 2 | `fsk_bits(samples, phase, ...)` | 1ビット区間ごとに1200Hz/1800Hzの強さを比較し、ビット列にする |
| 3 | `goertzel(blocks, freq, sample_rate)` | 指定周波数の成分の強さを計算する |
| 4 | `diff_bits(bits)` | differential decodeを行う |
| 5 | `find_flags(bits)` | HDLCフラグ `0x7e` を探す |
| 6 | `unstuff(bits)` | HDLCのbit stuffingを解除する |
| 7 | `bits_to_bytes_lsb(bits)` | ビット列をバイト列へ戻す |
| 8 | `crc16_x25(data)` | X.25 CRCでフレームが正しいか確認する |
| 9 | `parse_mod_info(info)` | info部の `:MOD` からGPSペイロードを読む |
| 10 | `parse_dms_bcd(buf)` | BCD/DMS形式の緯度・経度を十進度へ変換する |
| 11 | `parse_u16_bcd(buf)` | BCD形式の高度などを数値へ変換する |
| 12 | `decode_samples(samples, ...)` | 上記全体をまとめて実行し、`GpsFix` を返す |

### 入力データ

単一音声チャンネルのPCM配列です。

### 中間データ

```text
PCM音声
  ↓
1200Hz/1800Hz判定結果
  ↓
1200baudビット列
  ↓
HDLC/AX.25風フレーム
  ↓
:MODペイロード
```

### 出力データ

`GpsFix` です。

```text
sample_offset
phase
group
aircraft
lat
lon
alt
payload_hex
```

## 4. GPS CSV保存

### コンテナ

`get_heri_gps`

### 主なファイル

`gps_receiver/app.py`

### 関数

| 関数 | 役割 |
|---|---|
| `CsvWriter.__init__(path)` | CSVを開き、空ファイルならヘッダを書く |
| `CsvWriter.write(row)` | 1件のGPSレコードをCSVへ追記する |
| `format_japanese_time(dt)` | `YYYY/MM/DD HH:MM:SS` の日本向け表記へ変換する |
| `worker_main()` | `GpsFix` からCSV行を作る |

### 保存先

コンテナ内:

```text
/app/output/gps_positions.csv
```

ホスト側:

```text
gps_receiver/output/gps_positions.csv
```

### CSV列

```text
time,source,channel,offset_sec,lon,lat,alt,group,aircraft,payload_hex
```

## 5. 逆ジオコーダーへの送信

### 送信元コンテナ

`get_heri_gps`

### 送信先コンテナ

`reverse_geocoder`

### API

```text
POST http://reverse-geocoder:8020/api/position
```

### 関数

`gps_receiver/app.py`

| 関数 | 役割 |
|---|---|
| `post_reverse_geocode(config, row)` | 緯度・経度・高度などをJSONで逆ジオコーダーへ送信する |
| `AppState.mark_geocode_success(geocode)` | 地名変換成功数と最新地名を更新する |
| `AppState.mark_geocode_error(error)` | 地名変換エラーを記録する |

### 送信データ

```json
{
  "time": "2026/06/14 02:46:56",
  "lat": 34.0,
  "lon": 135.0,
  "alt": 281,
  "source": "get_heri_gps",
  "channel": 2
}
```

## 6. 逆ジオコーディング

### コンテナ

`reverse_geocoder`

### 主なファイル

```text
reverse_geocoder/app.py
reverse_geocoder/geocoder.py
```

### 関数

`reverse_geocoder/app.py`

| 関数 | 役割 |
|---|---|
| `post_position(payload)` | `/api/position`。緯度・経度を受け取り、地名を返す |
| `append_csv(row)` | 地名変換結果をCSVへ追記する |
| `get_latest()` | `/api/latest`。最新の地名付き位置を返す |
| `get_history()` | `/api/history`。直近履歴を返す |
| `health()` | `/api/health`。DB読込状態と行政区域件数を返す |

`reverse_geocoder/geocoder.py`

| 関数 | 役割 |
|---|---|
| `AdminGeocoder.reverse(lat, lon)` | 緯度・経度が含まれる行政区域をSQLite DBから探す |
| `point_in_polygon(lon, lat, rings)` | 行政区域ポリゴン内に点があるか判定する |
| `point_in_ring(lon, lat, ring)` | 1つの輪郭内に点があるか判定する |

### 処理

```text
緯度・経度を受け取る
  ↓
SQLiteのareasテーブルからbounding boxで候補を絞る
  ↓
point_in_polygon()で実際に行政区域内か判定
  ↓
都道府県・市区町村を返す
  ↓
CSVへ保存
  ↓
latest/historyに保持
```

### 保存先

コンテナ内:

```text
/app/output/geocoded_positions.csv
```

ホスト側:

```text
reverse_geocoder/output/geocoded_positions.csv
```

## 7. テロップ用地名取得

### コンテナ

`telop_output`

### 主なファイル

`telop_output/app.py`

### API

`telop_output` は `reverse_geocoder` から最新地名を取得します。

```text
GET http://reverse-geocoder:8020/api/latest
```

### 関数

| 関数 | 役割 |
|---|---|
| `get_latest_geocode()` | 逆ジオコーダーの `/api/latest` を読む |
| `render_text(config, geocode)` | `text_template` に地名を埋め込み、表示文字列を作る |

### 入力データ

```json
{
  "ok": true,
  "address_label": "大阪府大阪市",
  "prefecture": "大阪府",
  "city": "大阪市",
  "lat": 34.0,
  "lon": 135.0
}
```

## 8. V/Keyプレビュー生成

### コンテナ

`telop_output`

### 主なファイル

`telop_output/app.py`

### 関数

| 関数 | 役割 |
|---|---|
| `available_fonts()` | 使用可能な日本語フォント一覧を作る |
| `find_font(font_family)` | UI設定に合うフォントファイルを選ぶ |
| `fit_font(draw, text, font_path, ...)` | 指定枠内に収まるフォントサイズへ調整する |
| `render_rgba(config, key_background_opacity=None)` | 透明背景RGBA画像としてテロップを描画する |
| `preview_v()` | `/api/preview/v.png`。Vプレビュー画像を返す |
| `preview_key()` | `/api/preview/key.png`。Keyプレビュー画像を返す |
| `png_response(img)` | PIL画像をPNGレスポンスへ変換する |

### 処理

```text
最新地名を取得
  ↓
text_templateから表示文字列を作る
  ↓
UI設定のフォント・色・縁取り・マット・位置を読む
  ↓
PillowでRGBA画像に描画
  ↓
VプレビューまたはKeyプレビューとしてPNG返却
```

### API

```text
GET /api/preview/v.png
GET /api/preview/key.png
GET /api/fonts
GET /api/config
POST /api/config
```

## 9. UI表示

### コンテナ

`get_heri_gps`

### 主なファイル

```text
gps_receiver/templates/index.html
gps_receiver/static/app.js
```

### UIが呼ぶ主なAPI

`gps_receiver/app.py`

| API | 関数 | 役割 |
|---|---|---|
| `GET /api/status` | `status()` | 現在状態を返す |
| `GET /api/devices` | `devices()` | 入力デバイス一覧を返す |
| `POST /api/config` | `set_config()` | GPS入力設定を保存する |
| `POST /api/start` | `start()` | GPS取得を開始する |
| `POST /api/stop` | `stop()` | GPS取得を停止する |
| `GET /api/download` | `download()` | GPS CSVを返す |
| `WebSocket /ws` | `websocket()` | 0.5秒ごとに状態を送る |

テロップ関連は、`gps_receiver` が `telop_output` へプロキシします。

| UI側API | `gps_receiver/app.py` の関数 | 実際の転送先 |
|---|---|---|
| `GET /api/telop/status` | `telop_status()` | `telop_output /api/status` |
| `GET /api/telop/output-devices` | `telop_output_devices()` | `telop_output /api/output-devices` |
| `GET /api/telop/fonts` | `telop_fonts()` | `telop_output /api/fonts` |
| `GET /api/telop/config` | `telop_config()` | `telop_output /api/config` |
| `POST /api/telop/config` | `telop_set_config()` | `telop_output /api/config` |
| `POST /api/telop/start` | `telop_start()` | `telop_output /api/start` |
| `POST /api/telop/stop` | `telop_stop()` | `telop_output /api/stop` |
| `GET /api/telop/preview/v.png` | `telop_preview("v")` | `telop_output /api/preview/v.png` |
| `GET /api/telop/preview/key.png` | `telop_preview("key")` | `telop_output /api/preview/key.png` |

### JavaScript側の主な関数

`gps_receiver/static/app.js`

| 関数 | 役割 |
|---|---|
| `loadDevices()` | 入力デバイスのプルダウンを作る |
| `applyConfig()` | 入力設定を `/api/config` へ保存する |
| `start()` | 設定保存後に `/api/start` を呼ぶ |
| `stop()` | `/api/stop` を呼ぶ |
| `connect()` | WebSocketへ接続し、状態を受け取る |
| `update(payload)` | 最新位置、CSVパス、地名、履歴、ボタン状態を更新する |
| `loadTelopDevices()` | V/Key出力デバイス一覧を読む |
| `loadTelopFonts()` | フォント一覧を読む |
| `applyTelopConfig()` | テロップ設定を保存する |
| `refreshTelopPreview()` | V/Keyプレビュー画像を更新する |

## 10. データ保存先まとめ

| データ | コンテナ内 | ホスト側 |
|---|---|---|
| GPS CSV | `/app/output/gps_positions.csv` | `gps_receiver/output/gps_positions.csv` |
| オフライン復調CSV | `/app/output/demodulated_gps.csv` | `gps_receiver/output/demodulated_gps.csv` |
| 逆ジオコードCSV | `/app/output/geocoded_positions.csv` | `reverse_geocoder/output/geocoded_positions.csv` |
| 行政区域DB | `/app/data/admin_area.sqlite` | `reverse_geocoder/data/admin_area.sqlite` |
| テロップ設定 | `/app/config/telop_config.json` | `telop_output/config/telop_config.json` |
| 追加フォント | `/app/assets/fonts/` | `telop_output/assets/fonts/` |

## 11. コンテナ間通信まとめ

| From | To | API | 内容 |
|---|---|---|---|
| `get_heri_gps` | `reverse_geocoder` | `POST /api/position` | 緯度・経度・高度を送り、地名を受け取る |
| `telop_output` | `reverse_geocoder` | `GET /api/latest` | 最新地名を取得する |
| `get_heri_gps` | `telop_output` | `GET/POST /api/...` | UIからのテロップ設定・プレビュー取得を中継する |

## 12. 設定ファイルの役割

| ファイル | 役割 |
|---|---|
| `.env` | ルート一括起動用。ホスト側ポート、マウントパス、各サービスenvファイルの場所 |
| `gps_receiver/.env` | GPS入力デバイス、復調パラメータ、CSV保存先、逆ジオコーダーURL |
| `reverse_geocoder/.env` | 地名DB、国土数値情報URL、逆ジオコードCSV保存先 |
| `telop_output/.env` | 最新地名取得URL、テロップ設定ファイルパス |

別サーバへ分ける場合は、各フォルダを個別に配置し、そのフォルダの `.env` 内の接続先URLを実サーバIPまたはホスト名へ変更します。

## 13. ログ出力

実SDI受信中は、主要なデータフローが各コンテナのローテーションログに出ます。

| コンテナ | ログファイル | 主な内容 |
|---|---|---|
| `get_heri_gps` | `gps_receiver/logs/gps_receiver.log` | 音声入力開始、入力進捗、復調成功、GPS座標、CSV保存、逆ジオ送信 |
| `reverse_geocoder` | `reverse_geocoder/logs/reverse_geocoder.log` | 緯度経度受信、行政区域検索結果、地名CSV保存 |
| `telop_output` | `telop_output/logs/telop_output.log` | 最新地名取得、テロップ文字列変更、設定保存、開始/停止 |

ログはPythonの `RotatingFileHandler` でローテーションします。デフォルトは以下です。

```text
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=5
```

つまり、各ログは約5MBでローテーションし、過去5世代まで保持します。

Docker標準出力ログにも上限を付けています。

```text
DOCKER_LOG_MAX_SIZE=10m
DOCKER_LOG_MAX_FILE=3
```

ログを追う例:

```bash
tail -f gps_receiver/logs/gps_receiver.log
tail -f reverse_geocoder/logs/reverse_geocoder.log
tail -f telop_output/logs/telop_output.log
```

GPS受信時に特に見るべき流れ:

```text
gps_receiver:
  flow=input mode=sdi
  flow=input progress
  flow=demod decode_ok
  flow=gps fix
  flow=csv write
  flow=reverse_geocode post

reverse_geocoder:
  flow=geocoder receive
  flow=geocoder success
  flow=geocoder csv_write

telop_output:
  flow=telop text_update
```
