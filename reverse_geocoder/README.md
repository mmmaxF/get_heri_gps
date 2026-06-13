# reverse_geocoder

`get_heri_gps` からPOSTされた緯度・経度を、ローカル行政区域DBで都道府県・市区町村に変換するコンテナです。

## データ

デフォルトでは、国土数値情報 N03 行政区域データの近畿地方版を起動時に取得します。

```text
https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/N03-20260101_56_GML.zip
```

DBがすでにあり、更新日数内なら再取得しません。

## API

```text
POST /api/position
GET  /api/latest
GET  /api/history
GET  /api/health
```

POST例:

```json
{
  "time": "2026-06-13T13:24:04+09:00",
  "lat": 34.591708,
  "lon": 135.574025,
  "alt": 281,
  "source": "get_heri_gps",
  "channel": 2
}
```
