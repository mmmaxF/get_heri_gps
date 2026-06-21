# Reverse Geocoding Specification

作成日: 2026-06-14

この仕様書は、`get_heri_gps` で取得した緯度・経度から、都道府県・市区町村名をリアルタイムに表示するための設計をまとめたものです。

## 1. 目的

`get_heri_gps` はSDI音声からGPSを復調し、緯度・経度・高度を取得します。
次の段階として、取得した緯度・経度を逆ジオコーディングし、8010ポートの既存UI上に地名を表示します。

必要な地名粒度:

```text
〇〇県・〇〇市まで
```

町丁目や番地までは不要です。

## 2. 全体構成

採用方式は以下です。

```text
API push + CSV保存
```

構成:

```text
get_heri_gps
  - SDI音声からGPSを復調
  - gps_positions.csv に保存
  - reverse_geocoder へ HTTP POST
  - 逆ジオコード結果を8010 UIに表示

reverse_geocoder
  - POST /api/position で緯度経度を受信
  - ローカル行政区域DBで都道府県・市区町村を判定
  - 結果をJSONで返す
  - geocoded_positions.csv に保存
```

## 3. コンテナ構成

`docker-compose.yml` 上では、将来的に以下の2サービス構成にします。

```yaml
services:
  get-heri-gps:
    ports:
      - "8010:8010"
    environment:
      REVERSE_GEOCODER_URL: "http://reverse-geocoder:8020/api/position"
    volumes:
      - "./output:/app/output"

  reverse-geocoder:
    build: ./reverse_geocoder
    container_name: reverse_geocoder
    ports:
      - "8020:8020"
    volumes:
      - "./reverse_geocoder/data:/app/data"
      - "./reverse_geocoder/output:/app/output"
```

Docker内部では、`get-heri-gps` から以下のURLで逆ジオコーダーへ到達できます。

```text
http://reverse-geocoder:8020/api/position
```

## 4. get_heri_gps側の仕様

### 4.1 GPS CSV保存

従来どおり、GPS復調結果はCSVに保存します。

```text
output/gps_positions.csv
```

列:

```text
time,source,channel,offset_sec,lon,lat,alt,group,aircraft,payload_hex
```

### 4.2 逆ジオコーダーへのPOST

GPSを1件復調するたびに、`REVERSE_GEOCODER_URL` へHTTP POSTします。

POST先:

```text
POST /api/position
```

リクエスト例:

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

タイムアウト:

```text
0.8秒
```

逆ジオコーダーが未起動、またはエラーでもGPS取得処理は止めません。
GPS CSV保存は継続します。

### 4.3 8010 UI表示

既存の8010 UIに以下を表示します。

```text
最新位置:
  地名
  緯度
  経度
  高度

メトリクス:
  地名変換件数

受信履歴:
  時刻
  緯度
  経度
  高度
  地名
  入力
```

逆ジオコーダーが未接続の場合、地名は `-` のままです。

## 5. reverse_geocoder側のAPI仕様

### 5.1 POST /api/position

緯度・経度を受け取り、都道府県・市区町村を返します。

レスポンス例:

```json
{
  "ok": true,
  "time": "2026-06-13T13:24:04+09:00",
  "lat": 34.591708,
  "lon": 135.574025,
  "prefecture": "大阪府",
  "city": "堺市",
  "address_label": "大阪府堺市",
  "admin_code": "27140"
}
```

該当区域が見つからない場合:

```json
{
  "ok": false,
  "error": "area not found",
  "lat": 34.591708,
  "lon": 135.574025
}
```

### 5.2 GET /api/latest

最後に逆ジオコードした位置を返します。

### 5.3 GET /api/health

起動状態とDBロード状態を返します。

例:

```json
{
  "ok": true,
  "db_loaded": true,
  "area_count": 1900
}
```

## 6. 地名DB方式

〇〇県・〇〇市まででよいため、住所代表点の最近傍検索ではなく、行政区域ポリゴン判定を使います。

方式:

```text
緯度経度
  ↓
行政区域ポリゴンに含まれるか判定
  ↓
都道府県・市区町村を返す
```

使用データ:

```text
国土数値情報 行政区域データ N03
```

このデータには、都道府県・市区町村名と境界ポリゴンが含まれています。

## 7. DB作成方式

行政区域データをSQLiteへ変換します。

DB:

```text
admin_area.sqlite
```

テーブル案:

```sql
CREATE TABLE areas (
  id INTEGER PRIMARY KEY,
  prefecture TEXT NOT NULL,
  city TEXT NOT NULL,
  admin_code TEXT,
  min_lat REAL NOT NULL,
  max_lat REAL NOT NULL,
  min_lon REAL NOT NULL,
  max_lon REAL NOT NULL,
  geometry_json TEXT NOT NULL
);

CREATE INDEX idx_areas_bbox
ON areas(min_lat, max_lat, min_lon, max_lon);
```

`geometry_json` にはPolygonまたはMultiPolygonの座標列をJSONで保存します。

## 8. リアルタイム照合方式

reverse_geocoder起動時:

```text
1. admin_area.sqlite を開く
2. 行政区域ポリゴンをロード
3. bbox情報を使える状態にする
```

POST受信時:

```text
1. lat/lonを受信
2. bboxで候補区域を絞る
3. point-in-polygon 判定
4. 一致した区域の prefecture/city を返す
5. geocoded_positions.csv に保存
```

bbox絞り込みSQL:

```sql
SELECT *
FROM areas
WHERE min_lat <= :lat
  AND max_lat >= :lat
  AND min_lon <= :lon
  AND max_lon >= :lon;
```

候補に対してPython側でpoint-in-polygon判定を行います。

## 9. 出力CSV

reverse_geocoder側でも、住所付きCSVを保存します。

```text
reverse_geocoder/output/geocoded_positions.csv
```

列:

```text
time,lon,lat,alt,prefecture,city,address_label,admin_code
```

例:

```csv
2026-06-13T13:24:04+09:00,135.574025,34.591708,281,大阪府,堺市,大阪府堺市,27140
```

## 10. 障害時の扱い

### reverse_geocoderが停止している場合

`get_heri_gps` はPOST失敗を記録しますが、GPS取得とCSV保存は止めません。

```text
GPS取得: 継続
gps_positions.csv: 継続保存
8010 UI地名: "-" または未接続
```

### 行政区域が見つからない場合

reverse_geocoderは `ok=false` を返します。
海上や境界外では起こり得ます。

### 後追い補完

GPS CSVは残っているため、必要なら後から `gps_positions.csv` を読み直して `geocoded_positions.csv` を再生成できます。

## 11. 今後の実装順

推奨順:

```text
1. reverse_geocoder コンテナの雛形作成
2. POST /api/position 実装
3. ダミーDBで8010 UI表示確認
4. 国土数値情報 N03 から admin_area.sqlite 作成
5. point-in-polygon 実装
6. geocoded_positions.csv 保存
7. docker-compose.yml に reverse-geocoder サービス追加
```

## 12. 現時点のget_heri_gps側実装

現時点で `get_heri_gps` には以下を追加済みです。

```text
REVERSE_GEOCODER_URL 設定
GPS取得時の HTTP POST
POST失敗時もGPS処理を止めない
8010 UI上の地名表示欄
地名変換件数表示
受信履歴への地名列追加
```

reverse_geocoderコンテナが未実装/未起動の場合、地名欄は空のままですが、GPS取得には影響しません。

## 13. 実装メモ

実装では、国土数値情報 N03 の近畿地方版をデフォルト取得対象にします。

```text
https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2026/N03-20260101_56_GML.zip
```

取得対象を変えたい場合は、`docker-compose.yml` の `GEOCODER_DATA_URL` を変更します。

例:

```text
全国版、都道府県版、地方版のN03 zip URL
```
