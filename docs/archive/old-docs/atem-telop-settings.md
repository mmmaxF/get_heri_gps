# ATEMテロップ設定変更手順

ATEMへ送るPNGテロップは、主に次の2つの `.env` で制御します。

| ファイル | 役割 |
|---|---|
| `reverse_geocoder/.env` | 表示文字列の組み立て、撮影位置計算、ATEM送信間隔 |
| `atem_output/.env` | PNG描画位置、フォント、実機ATEM送信 |

`.env` を変更した後は、対象サービスを再起動してください。

```bash
docker compose up -d --build reverse-geocoder atem-output
```

## 現在地行の表示形式

現在地行は、通常この形式です。

```text
YTV ＜HH:MM＞ ＜現在地住所＞ 上空
```

関連設定は `reverse_geocoder/.env` と `atem_output/.env` の両方にあります。

```env
ATEM_TEXT_TEMPLATE={atem_header} {address_label} 上空
ATEM_TEXT_HEADER_ENABLED=1
ATEM_TEXT_HEADER_TEMPLATE={atem_station} {atem_time}
ATEM_TEXT_STATION_ENABLED=1
ATEM_TEXT_STATION_TEMPLATE=YTV
ATEM_TEXT_TIME_ENABLED=1
ATEM_TEXT_TIME_TEMPLATE={hhmm}
```

### 現在地行の `YTV` を消す

```env
ATEM_TEXT_STATION_ENABLED=0
```

表示例:

```text
10:55 大阪府岸和田市 上空
```

### 現在地行の時刻を消す

```env
ATEM_TEXT_TIME_ENABLED=0
```

表示例:

```text
YTV 大阪府岸和田市 上空
```

## 撮影位置行の表示形式

撮影位置行は、通常この形式です。

```text
YTV ＜HH:MM＞ ＜撮影位置住所＞ 撮影
```

関連設定は `reverse_geocoder/.env` と `atem_output/.env` の両方にあります。

```env
ATEM_CAPTURE_LINE_ENABLED=1
ATEM_CAPTURE_STATION_ENABLED=1
ATEM_CAPTURE_TIME_ENABLED=1
ATEM_CAPTURE_HEADER_TEMPLATE={atem_capture_station} {atem_capture_time}
ATEM_CAPTURE_LINE_TEMPLATE={atem_capture_header} {capture_address_label} 撮影
```

### 撮影位置行を消す

```env
ATEM_CAPTURE_LINE_ENABLED=0
```

### 撮影位置行だけ `YTV` を消す

```env
ATEM_CAPTURE_STATION_ENABLED=0
```

表示例:

```text
YTV 10:55 大阪府岸和田市 上空
10:55 大阪府泉南市 撮影
```

### 撮影位置行だけ時刻を消す

```env
ATEM_CAPTURE_TIME_ENABLED=0
```

表示例:

```text
YTV 10:55 大阪府岸和田市 上空
YTV 大阪府泉南市 撮影
```

### 撮影位置行だけ `YTV` と時刻を両方消す

```env
ATEM_CAPTURE_STATION_ENABLED=0
ATEM_CAPTURE_TIME_ENABLED=0
```

表示例:

```text
YTV 10:55 大阪府岸和田市 上空
大阪府泉南市 撮影
```

## 撮影位置不明時の表示

撮影位置の逆ジオコードに失敗した時、2行目を出すかどうかを制御できます。

```env
ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN=1
ATEM_CAPTURE_LINE_UNKNOWN_LABEL=撮影位置不明
```

`1` の場合:

```text
YTV 10:55 大阪府岸和田市 上空
YTV 10:55 撮影位置不明 撮影
```

`0` の場合は、撮影位置住所が取れない時に2行目を出しません。

```env
ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN=0
```

## 撮影位置計算のON/OFF

撮影位置計算自体は `reverse_geocoder/.env` で制御します。

```env
CAPTURE_LOCATION_ENABLED=1
```

OFFにする場合:

```env
CAPTURE_LOCATION_ENABLED=0
ATEM_CAPTURE_LINE_ENABLED=0
```

撮影位置計算で使う推定フィールドは以下です。

```env
CAPTURE_CAMERA_HEADING_BCD_OFFSET=31
CAPTURE_CAMERA_HEADING_SCALE=0.1
CAPTURE_CAMERA_TILT_BCD_OFFSET=29
CAPTURE_CAMERA_TILT_SCALE=0.01
CAPTURE_TILT_MIN_DEGREES=1.0
CAPTURE_MAX_DISTANCE_METERS=10000
```

現状はSDI 4chの未解析ペイロードからの仮推定です。カメラ方位・チルトのバイト位置が確定したら、この値を調整します。

## 表示位置を変える

PNG上の表示位置は `atem_output/.env` で変えます。

```env
POSITION_X=1850
POSITION_Y=100
POSITION_ANCHOR=top_right
TEXT_ALIGN=right
```

下へずらす場合は `POSITION_Y` を大きくします。

```env
POSITION_Y=150
```

## ATEM送信方式

ATEMへのMedia Poolアップロードは重めの処理です。通常は非同期送信をONにして、HTTP APIを詰まらせないようにします。

```env
ATEM_ASYNC_UPLOAD=1
```

`1` の場合、`atem-output` は以下の動きになります。

```text
/api/position
  ↓
PNG生成
  ↓
最新PNGを保存
  ↓
ATEM送信workerへ最新ジョブを渡す
  ↓
HTTPはすぐ応答

ATEM送信worker
  ↓
古い未送信PNGは捨てる
  ↓
最新PNGだけATEMへ送る
```

ATEMへの送信をAPIリクエスト内で同期実行したい場合だけ、以下にします。

```env
ATEM_ASYNC_UPLOAD=0
```

通常運用では `1` のままにしてください。

## 反映確認

再起動後、APIで現在のATEMテキストを確認します。

```bash
curl -s http://127.0.0.1:8030/api/latest | python3 -m json.tool
```

逆ジオ側の撮影位置計算結果を見る場合:

```bash
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
```

ログ確認:

```bash
tail -f reverse_geocoder/logs/reverse_geocoder.log
tail -f atem_output/logs/atem_output.log
```
