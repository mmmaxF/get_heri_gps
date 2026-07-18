# ATEMスーパー

## 役割

`atem-output` は `reverse-geocoder` から受け取った文字列をPNG化し、ATEMへ送ります。

```text
reverse-geocoder
  ↓ POST /api/position
atem-output
  ↓ latest.png生成
  ↓ worker threadでATEMへ送信
ATEM Media Pool / Media Player / DSK
```

## 現在地行

標準表示:

```text
YTV ＜HH:MM＞ ＜現在地住所＞ 上空
```

設定は `reverse_geocoder/.env` と `atem_output/.env` の両方にあります。

```env
ATEM_TEXT_TEMPLATE={atem_header} {address_label} 上空
ATEM_TEXT_HEADER_ENABLED=1
ATEM_TEXT_HEADER_TEMPLATE={atem_station} {atem_time}
ATEM_TEXT_STATION_ENABLED=1
ATEM_TEXT_STATION_TEMPLATE=YTV
ATEM_TEXT_TIME_ENABLED=1
ATEM_TEXT_TIME_TEMPLATE={hhmm}
```

`YTV` を消す:

```env
ATEM_TEXT_STATION_ENABLED=0
```

時刻を消す:

```env
ATEM_TEXT_TIME_ENABLED=0
```

## 撮影位置行

標準表示:

```text
YTV ＜HH:MM＞ ＜撮影位置住所＞ 撮影
```

設定:

```env
ATEM_CAPTURE_LINE_ENABLED=1
ATEM_CAPTURE_STATION_ENABLED=1
ATEM_CAPTURE_TIME_ENABLED=1
ATEM_CAPTURE_HEADER_TEMPLATE={atem_capture_station} {atem_capture_time}
ATEM_CAPTURE_LINE_TEMPLATE={atem_capture_header} {capture_address_label} 撮影
```

撮影位置行を消す:

```env
ATEM_CAPTURE_LINE_ENABLED=0
```

撮影位置行だけ `YTV` を消す:

```env
ATEM_CAPTURE_STATION_ENABLED=0
```

撮影位置行だけ時刻を消す:

```env
ATEM_CAPTURE_TIME_ENABLED=0
```

## 撮影位置不明

撮影位置の逆ジオコードに失敗した場合の表示です。

```env
ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN=1
ATEM_CAPTURE_LINE_UNKNOWN_LABEL=撮影位置不明
```

表示例:

```text
YTV 16:10 大阪府堺市 上空
YTV 16:10 撮影位置不明 撮影
```

不明時に2行目を出さない:

```env
ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN=0
```

## 撮影位置計算

`reverse_geocoder/.env` で制御します。

```env
CAPTURE_LOCATION_ENABLED=1
CAPTURE_CAMERA_HEADING_BCD_OFFSET=31
CAPTURE_CAMERA_HEADING_SCALE=0.1
CAPTURE_CAMERA_TILT_BCD_OFFSET=29
CAPTURE_CAMERA_TILT_SCALE=0.01
CAPTURE_TILT_MIN_DEGREES=1.0
CAPTURE_MAX_DISTANCE_METERS=10000
```

現状はSDI 4chの未解析ペイロードからの仮推定です。  
撮影位置は以下で求めています。

```text
ヘリ緯度経度 + 高度 + カメラ方位 + チルト角
↓
地表交点を計算
↓
逆ジオコード
```

## PNG描画位置

`atem_output/.env`:

```env
POSITION_X=1850
POSITION_Y=50
POSITION_ANCHOR=top_right
TEXT_ALIGN=right
FONT_SIZE=27
FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
TEXT_STROKE_WIDTH=2
TEXT_STROKE_COLOR=0,0,0,255
MATTE_ENABLED=0
```

下へずらす場合:

```env
POSITION_Y=100
```

## ATEM送信

`atem_output/.env`:

```env
ATEM_ENABLED=1
ATEM_HOST=192.168.11.65
ATEM_MEDIA_POOL_SLOT=1
ATEM_MEDIA_PLAYER=1
ATEM_DSK=1
ATEM_FILL_SOURCE=3010
ATEM_KEY_SOURCE=3011
ATEM_ON_AIR=1
ATEM_UPLOAD_COMPRESS=1
ATEM_PERSISTENT_CONNECTION=1
ATEM_ASYNC_UPLOAD=1
```

`ATEM_ASYNC_UPLOAD=1` の場合、HTTP APIはPNG生成とジョブ投入だけ行い、ATEM送信はworker threadが行います。未送信の古いPNGは削除し、最新だけ送ります。

## 反映

```bash
docker compose up -d --build reverse-geocoder atem-output
```

ATEM描画位置やIPだけなら:

```bash
docker compose up -d --build atem-output
```

確認:

```bash
curl -s http://127.0.0.1:8030/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8030/api/latest | python3 -m json.tool
```

