# Data Flow

このドキュメントは、SDI/HDMI/USB音声に載っているGPS信号が、CSV・地名・マルチビューアー表示コマンドへ変換されるまでの流れをまとめたものです。

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
  8. SQLiteの行政区域DBで都道府県・市区町村を検索
  9. geocoded_positions.csvへ保存
  10. 最新地名として保持
  11. マルチビューアーへTCPコマンドを送信
  ↓
マルチビューアー
  12. 受け取った地名テキストを表示
  ↓
gps_receiver UI
  13. 最新位置・地名・MV送信状態・受信履歴を表示
```

## コンテナ構成

| コンテナ | フォルダ | 主な役割 | ポート |
|---|---|---|---|
| `get_heri_gps` | `gps_receiver/` | SDI/HDMI/USB音声からGPSを復調し、メインUIを表示する | `8010` |
| `reverse_geocoder` | `reverse_geocoder/` | 緯度・経度を地名へ変換し、マルチビューアーへ送信する | `8020` |

`telop_output` コンテナによるV/Key画像生成は廃止しました。現在は、逆ジオコーダーで作った地名テキストをマルチビューアーへ直接TCP送信します。

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
| 11 | `decode_samples(samples, ...)` | 上記全体をまとめて実行し、`GpsFix` を返す |

## 4. GPS CSV保存

### コンテナ

`get_heri_gps`

### 保存先

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

| 関数 | 役割 |
|---|---|
| `post_reverse_geocode(config, row)` | 緯度・経度・高度などをJSONで逆ジオコーダーへ送信する |
| `AppState.mark_geocode_success(geocode)` | 地名変換成功数と最新地名を更新する |
| `AppState.mark_geocode_error(error)` | 地名変換エラーを記録する |

## 6. 地名変換

### コンテナ

`reverse_geocoder`

### 主なファイル

```text
reverse_geocoder/app.py
reverse_geocoder/geocoder.py
```

### 処理

```text
gps_receiverから緯度・経度を受け取る
post_position()
↓
SQLiteの行政区域DBで都道府県・市区町村を探す
AdminGeocoder.reverse()
↓
結果をCSVに保存する
append_csv()
↓
最新位置として保持する
latest / history
↓
地名付きデータを返す
post_position()
```

### 保存先

```text
reverse_geocoder/output/geocoded_positions.csv
```

### CSV列

```text
time,lon,lat,alt,prefecture,city,ward,address_label,admin_code
```

## 7. マルチビューアー送信

### コンテナ

`reverse_geocoder`

### 主なファイル

```text
reverse_geocoder/multiviewer.py
reverse_geocoder/app.py
```

### 処理

```text
逆ジオで地名付きデータを作る
post_position()
↓
送信用テキストを作る
multiviewer.render_text()
↓
STW010V010 + テキスト + CRLF を作る
multiviewer.send_text()
↓
Shift_JISでエンコードする
multiviewer.send_text()
↓
192.168.11.69:51069 へTCP送信する
multiviewer.send_text()
↓
OKなどの応答を受け取る
multiviewer.send_text()
↓
送信結果をAPIレスポンスと最新位置に含める
post_position()
```

デフォルトの送信コマンド:

```text
STW010V010{address_label}\r\n
```

例:

```text
STW010V010大阪府大阪市\r\n
```

### 設定

`reverse_geocoder/.env` で変更できます。

```text
MULTIVIEWER_ENABLED=1
MULTIVIEWER_HOST=192.168.11.69
MULTIVIEWER_PORT=51069
MULTIVIEWER_COMMAND_PREFIX=STW010V010
MULTIVIEWER_TEXT_TEMPLATE={address_label}
MULTIVIEWER_ENCODING=shift_jis
MULTIVIEWER_TIMEOUT_SECONDS=2.0
MULTIVIEWER_SEND_ON_NOT_FOUND=0
MULTIVIEWER_DEDUP_TEXT=1
```

`MULTIVIEWER_DEDUP_TEXT=1` の場合、同じ地名が連続したときは再送しません。

## API

### gps_receiver

| API | 関数 | 役割 |
|---|---|---|
| `GET /` | `index()` | UIを返す |
| `GET /api/status` | `status()` | 現在状態を返す |
| `GET /api/devices` | `devices()` | 入力デバイス一覧を返す |
| `POST /api/config` | `set_config()` | GPS入力設定を更新する |
| `POST /api/start` | `start()` | 復調ワーカーを開始する |
| `POST /api/stop` | `stop()` | 復調ワーカーを停止する |
| `GET /api/download` | `download()` | GPS CSVを返す |
| `WS /ws` | `websocket()` | UIへ状態を配信する |

### reverse_geocoder

| API | 関数 | 役割 |
|---|---|---|
| `GET /api/health` | `health()` | DB有無と行政区域件数を返す |
| `GET /api/latest` | `get_latest()` | 最新の地名付き位置とMV送信結果を返す |
| `GET /api/history` | `get_history()` | 直近履歴を返す |
| `POST /api/position` | `post_position()` | 緯度・経度を地名化し、MVへ送信する |

## ログ

| コンテナ | ログファイル | 主な内容 |
|---|---|---|
| `get_heri_gps` | `gps_receiver/logs/gps_receiver.log` | 入力、復調、GPS確定、CSV保存、逆ジオ送信 |
| `reverse_geocoder` | `reverse_geocoder/logs/reverse_geocoder.log` | 地名変換、地名CSV保存、マルチビューアー送信 |

マルチビューアー送信のログ例:

```text
flow=multiviewer sent host=192.168.11.69 port=51069 text=大阪府大阪市 response=OK
flow=multiviewer skipped reason=duplicate text text=大阪府大阪市
flow=multiviewer error host=192.168.11.69 port=51069 error=timed out
```

## 保存ファイル

| 種類 | コンテナ内 | ホスト側 |
|---|---|---|
| GPS CSV | `/app/output/gps_positions.csv` | `gps_receiver/output/gps_positions.csv` |
| 地名付きCSV | `/app/output/geocoded_positions.csv` | `reverse_geocoder/output/geocoded_positions.csv` |
| 行政区域DB | `/app/data/admin_area.sqlite` | `reverse_geocoder/data/admin_area.sqlite` |
| GPSログ | `/app/logs/gps_receiver.log` | `gps_receiver/logs/gps_receiver.log` |
| 逆ジオ/MVログ | `/app/logs/reverse_geocoder.log` | `reverse_geocoder/logs/reverse_geocoder.log` |
