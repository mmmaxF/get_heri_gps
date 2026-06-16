# gps_receiver 詳細設計

`gps_receiver` は、SDI/HDMI/USBキャプチャから入ってくる音声を読み、指定チャンネルに載っているGPS/MOD信号を復調してCSVへ保存するコンテナです。UIの中心でもあり、逆ジオコーダーとテロップ出力コンテナへの中継も行います。

## 役割

```text
入力デバイスの音声
  ↓
指定チャンネルだけを抽出
  ↓
1200/1800Hz FSK信号を復調
  ↓
HDLC/AX.25風フレームを検証
  ↓
:MODペイロードから緯度・経度・高度を抽出
  ↓
gps_positions.csvへ保存
  ↓
reverse_geocoderへHTTP POST
  ↓
UIへ最新状態を配信
```

## 主なファイル

| ファイル | 役割 |
|---|---|
| `app.py` | Web UI、API、実機音声入力、CSV保存、逆ジオコード送信、ワーカー制御 |
| `gps_demodulator.py` | PCM音声からGPS/MODを復調する本体 |
| `demodulate_gps.py` | 保存済みRAW/captureディレクトリを単体解析するCLI |
| `templates/index.html` | UIのHTML |
| `static/app.js` | UIの操作、WebSocket更新、テロップ設定操作 |
| `.env` | 実機ごとの入力デバイス、チャンネル、出力先など |

## 入力と出力

### 入力

実機入力ではALSAの録音デバイスを使います。

```text
signed 16-bit little-endian PCM
sample rate: 48000Hz
channels: INPUT_CHANNELS
```

通常はUIで入力デバイスを選ぶと、内部で次のようなコマンドが生成されます。

```bash
arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -t raw
```

### 出力

GPS復調結果はCSVへ追記されます。

```text
gps_receiver/output/gps_positions.csv
```

CSV列:

```text
time,source,channel,offset_sec,lon,lat,alt,group,aircraft,payload_hex
```

`time` は日本人が読みやすい `YYYY/MM/DD HH:MM:SS` 形式です。

## 設定値

主な設定は `gps_receiver/.env` に置きます。

| 変数 | 意味 |
|---|---|
| `HOST` / `PORT` | Webアプリの待受アドレスとポート |
| `INPUT_DEVICE` | 既定のALSA入力デバイス。例: `hw:2,0` |
| `INPUT_CHANNELS` | 入力音声全体のチャンネル数 |
| `GPS_CHANNEL` | GPS信号が入っている音声チャンネル番号。1始まり |
| `SAMPLE_RATE` | PCMサンプリング周波数。通常は `48000` |
| `OUTPUT_CSV` | GPS CSVの保存先 |
| `REVERSE_GEOCODER_URL` | 緯度・経度をPOSTする逆ジオコーダーAPI |
| `TELOP_OUTPUT_URL` | テロップ出力コンテナのAPIベースURL |
| `WINDOW_SECONDS` | 復調に使う音声バッファの保持秒数 |
| `DECODE_INTERVAL_SECONDS` | 復調を試す間隔 |
| `CAPTURE_DEVICE_INCLUDE_KEYWORDS` | UIに出す入力デバイス名の絞り込みキーワード |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | ログローテーション設定 |

## 起動時の流れ

`app.py` が起動すると、まず環境変数から既定値を読みます。

1. `setup_logger()` がローテーション付きログを作る。
2. `RuntimeConfig` が入力デバイス、GPSチャンネル、CSV保存先などを持つ。
3. `AppState` が実行状態、最新GPS、最新地名、履歴、ワーカー情報を保持する。
4. FastAPIが `/`、`/api/*`、`/ws` を公開する。

## UIとAPI

| API | 内容 |
|---|---|
| `GET /` | 操作用UI |
| `GET /api/status` | 現在状態、最新GPS、最新地名、履歴を返す |
| `GET /api/devices` | OSが認識している入力デバイス一覧を返す |
| `POST /api/config` | 入力デバイス、チャンネル、CSV保存先などを更新する |
| `POST /api/start` | 復調ワーカーを開始する |
| `POST /api/stop` | 復調ワーカーへ停止指示を出す |
| `GET /api/download` | GPS CSVをダウンロードする |
| `WS /ws` | UIへ0.5秒ごとに状態を配信する |

テロップ関連APIは `telop_output` へプロキシします。

| API | 転送先 |
|---|---|
| `GET /api/telop/status` | `telop_output /api/status` |
| `GET /api/telop/output-devices` | `telop_output /api/output-devices` |
| `GET /api/telop/fonts` | `telop_output /api/fonts` |
| `GET /api/telop/config` | `telop_output /api/config` |
| `POST /api/telop/config` | `telop_output /api/config` |

## 入力デバイス一覧の作り方

`list_capture_devices()` は `arecord -l` の結果を読みます。行から `card` と `device` を取り出し、`hw:カード番号,デバイス番号` の形にします。

その後、`CAPTURE_DEVICE_INCLUDE_KEYWORDS` に含まれるキーワードで絞り込みます。AJA、Blackmagic、SDI、MS2109、USB Audioなど、実際の入力候補だけをUIへ出すためです。

## 実機音声の読み取り

`iter_command_chunks(config, stop_event)` が実機入力の入口です。

1. `config.input_command` または `build_arecord_command()` でコマンドを決める。
2. `subprocess.Popen()` で `arecord` を起動する。
3. 0.25秒分ずつstdoutからRAW PCMを読む。
4. `numpy.frombuffer(..., dtype="<i2")` で16bit PCMの数値配列にする。
5. `INPUT_CHANNELS` でインターリーブ音声を行列化する。
6. `GPS_CHANNEL` で指定された1チャンネルだけ抜き出す。
7. 復調ワーカーへ `arr[:, ch_index]` を渡す。

ここでアプリが扱う「音声波形」は、A/D変換済みのPCM数値列です。アプリ自身がA/D変換するのではなく、AJAやUSBキャプチャ、OS/ALSAがデジタル音声として渡したものを読みます。

## テスト入力

`iter_test_chunks(config, stop_event)` は保存済みのRAWファイルを読むための入口です。

```text
capture_dir/
  metadata.json
  ch2.raw
```

`metadata.json` に録音開始時刻があれば、それを基準にCSV時刻を作ります。なければ現在時刻を使います。

## ワーカー処理

`worker_main()` がリアルタイム処理の中心です。

```text
STATE.mark_started()
  ↓
CsvWriterを開く
  ↓
実機またはテスト入力からPCMチャンクを読む
  ↓
WINDOW_SECONDS分だけバッファに保持
  ↓
DECODE_INTERVAL_SECONDSごとに decode_samples() を呼ぶ
  ↓
新規GPSだけをCSVへ保存
  ↓
reverse_geocoderへPOST
  ↓
STATEへ最新値を反映
```

重複保存を避けるため、`offset_sec` と `payload_hex` を組み合わせたキーを `seen` に保存しています。同じフレームが次の復調窓にも残っていても、CSVには1回だけ書かれます。

## 復調処理

復調の本体は `gps_demodulator.py` の `decode_samples()` です。

### 処理順

| 順番 | 関数 | 内容 |
|---|---|---|
| 1 | `normalize_samples()` | 音声の直流成分を取り、振幅を正規化する |
| 2 | `fsk_bits()` | 1ビット区間ごとに1200Hzと1800Hzの強さを比較する |
| 3 | `goertzel()` | 指定周波数の強さを計算する |
| 4 | `diff_bits()` | 差分符号化されたビットを元に戻す |
| 5 | `find_flags()` | HDLCフラグ `0x7e` を探す |
| 6 | `unstuff()` | HDLCのbit stuffingを解除する |
| 7 | `bits_to_bytes_lsb()` | ビット列をバイト列へ戻す |
| 8 | `crc16_x25()` | CRC-16/X.25を検証する |
| 9 | `parse_mod_info()` | info部の `:MOD` を解析する |
| 10 | `parse_dms_bcd()` | BCD/DMS形式の緯度・経度を十進度へ変換する |

### 1200Hz/1800Hz判定

`fsk_bits()` はサンプリング周波数48kHz、1200baudを前提に、1ビットを約40サンプルとして扱います。

```text
48000 samples/sec / 1200 bits/sec = 40 samples/bit
```

各40サンプルの中で、1200Hz成分と1800Hz成分のどちらが強いかを `goertzel()` で比較し、ビット値にします。

### フレーム検証

`decode_samples()` はHDLCフラグで区切られたフレームを取り出した後、次を確認します。

```text
frame[14] == 0x03
frame[15] == 0xF0
crc16_x25(frame) == 0xF0B8
info部が :MOD で始まる
```

CRCが合うということは、少なくともビット列の読み方、フレーム区切り、bit stuffing解除、バイト化の向きが合っている可能性が高いという意味です。

## GPS行の作成

`worker_main()` は `GpsFix` から次のCSV行を作ります。

| フィールド | 内容 |
|---|---|
| `time` | 日本時間の表示用時刻 |
| `time_iso` | 内部送信用のISO時刻。CSVヘッダには含めない |
| `source` | 入力元コマンドまたはテストディレクトリ |
| `channel` | GPS音声チャンネル |
| `offset_sec` | 入力開始からGPSフレーム位置までの秒数 |
| `lon` / `lat` | 十進度の経度・緯度 |
| `alt` | 高度 |
| `group` | MODペイロード内のグループ値 |
| `aircraft` | MODペイロード内の機体値 |
| `payload_hex` | MODペイロードの16進表記 |

## 逆ジオコーダーへの送信

`post_reverse_geocode(config, row)` が次のJSONを `REVERSE_GEOCODER_URL` へPOSTします。

```json
{
  "time": "2026/06/14 02:46:56",
  "lat": 34.00000000,
  "lon": 135.00000000,
  "alt": 100,
  "source": "get_heri_gps",
  "channel": 2
}
```

成功すると `STATE.mark_geocode_success()` が最新地名を更新します。失敗してもGPS CSV保存は続きます。

## ログ

ログはローテーションされ、コンテナ内では次に出ます。

```text
/app/logs/gps_receiver.log
```

ホスト側では次です。

```text
gps_receiver/logs/gps_receiver.log
```

主なログ:

| `flow=` | 意味 |
|---|---|
| `gps_receiver init` | 起動時設定 |
| `input mode=sdi` | 実機入力開始 |
| `input progress` | 読み取りサンプル数の進捗 |
| `demod decode_ok` | 復調でGPS候補が出た |
| `gps fix` | 緯度・経度・高度が確定した |
| `reverse_geocode post` | 逆ジオコーダーへ送信 |
| `csv write` | GPS CSVへ追記 |
| `worker error` | ワーカー例外 |

## 別サーバ化するときの注意

`gps_receiver` を単独サーバへ置く場合、少なくとも以下を変更します。

```text
gps_receiver/.env
  REVERSE_GEOCODER_URL=http://<reverse_geocoderサーバ>:8020/api/position
  TELOP_OUTPUT_URL=http://<telop_outputサーバ>:8030
```

入力デバイスを使うため、Dockerでは `/dev/snd` のマウントと音声デバイス権限が必要です。

