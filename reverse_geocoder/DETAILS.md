# reverse_geocoder 詳細設計

`reverse_geocoder` は、`gps_receiver` から送られてきた緯度・経度を、ローカルDBで都道府県・市区町村へ変換するコンテナです。外部APIへ毎回問い合わせる方式ではなく、起動時に行政区域データからSQLite DBを作り、リアルタイム処理中はローカル検索だけで応答します。

## 役割

```text
gps_receiverから緯度・経度を受信
  ↓
SQLiteの行政区域DBを検索
  ↓
都道府県・市区町村を特定
  ↓
geocoded_positions.csvへ保存
  ↓
最新地名をAPIで保持
```

## 主なファイル

| ファイル | 役割 |
|---|---|
| `app.py` | FastAPI、受信API、CSV保存、最新地名保持 |
| `geocoder.py` | 緯度・経度から行政区域ポリゴンを検索する本体 |
| `import_admin_areas.py` | 国土数値情報N03データを取得し、SQLite DBを作る |
| `entrypoint.sh` | コンテナ起動時にDB更新を行い、アプリを起動する |
| `.env` | DBパス、データURL、更新間隔、ログ設定 |

## 入力と出力

### 入力

`gps_receiver` が `POST /api/position` へJSONを送ります。

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

### 出力

APIレスポンス:

```json
{
  "ok": true,
  "prefecture": "大阪府",
  "city": "大阪市",
  "ward": "",
  "address_label": "大阪府大阪市",
  "admin_code": "27100",
  "time": "2026/06/14 02:46:56",
  "lat": 34.0,
  "lon": 135.0,
  "alt": 100,
  "source": "get_heri_gps",
  "channel": 2
}
```

CSV:

```text
reverse_geocoder/output/geocoded_positions.csv
```

CSV列:

```text
time,lon,lat,alt,prefecture,city,ward,address_label,admin_code
```

## 設定値

主な設定は `reverse_geocoder/.env` に置きます。

| 変数 | 意味 |
|---|---|
| `HOST` / `PORT` | APIの待受アドレスとポート |
| `GEOCODER_DB_PATH` | SQLite DBの保存先 |
| `GEOCODER_OUTPUT_CSV` | 地名付きCSVの保存先 |
| `GEOCODER_AUTO_UPDATE` | 起動時にDB更新を試すか。`0` なら更新しない |
| `GEOCODER_DATA_URL` | 国土数値情報N03 zipの取得URL |
| `GEOCODER_UPDATE_DAYS` | 既存DBを新鮮とみなす日数 |
| `GEOCODER_FORCE_UPDATE` | `1` なら既存DBがあっても再取得する |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | ログローテーション設定 |

## 起動時のDB準備

コンテナ起動時は `entrypoint.sh` が動きます。

```text
GEOCODER_DB_PATHを決める
  ↓
GEOCODER_AUTO_UPDATE != 0 なら import_admin_areas.py を実行
  ↓
失敗しても既存DBがあれば続行
  ↓
DBが存在しなければ空DBを作る
  ↓
python /app/app.py を起動
```

DB更新に失敗しても、過去に作ったDBがあればサービスは起動します。ネットワーク不調時に完全停止しにくくするためです。

## 行政区域DBの作り方

`import_admin_areas.py` が国土数値情報N03データをSQLiteへ変換します。

### 処理順

| 関数 | 内容 |
|---|---|
| `db_is_fresh(path)` | 既存DBがあり、件数があり、更新日数内なら再取得しない |
| `download(url, dest)` | N03 zipを取得する |
| `find_shapefile(root)` | zip展開後の `.shp` を探す |
| `field_map(reader)` | shapefileのフィールド名と位置を対応付ける |
| `get_value(record, fmap, name)` | レコードから都道府県、市区町村、行政コードなどを読む |
| `rings_from_shape(shape)` | ポリゴンの頂点列を取り出す |
| `bbox_from_rings(rings)` | 高速絞り込み用の外接矩形を作る |
| `build_db(zip_path, db_path, source_url)` | SQLite DBを作り直す |

### SQLiteテーブル

中心テーブルは `areas` です。

| カラム | 内容 |
|---|---|
| `prefecture` | 都道府県 |
| `city` | 市区町村 |
| `ward` | 区など。ない場合は空 |
| `admin_code` | 行政区域コード |
| `min_lat` / `max_lat` | ポリゴン外接矩形の緯度範囲 |
| `min_lon` / `max_lon` | ポリゴン外接矩形の経度範囲 |
| `geometry_json` | 行政区域ポリゴンの頂点列JSON |

`idx_areas_bbox` インデックスを使い、まず外接矩形で候補を減らします。

## 逆ジオコード処理

本体は `geocoder.py` の `AdminGeocoder.reverse(lat, lon)` です。

```text
緯度・経度を受け取る
  ↓
SQLで外接矩形に入る行政区域だけを取得
  ↓
各候補のgeometry_jsonを読む
  ↓
point_in_polygon()で本当にポリゴン内か判定
  ↓
最初に一致した行政区域を返す
```

### 高速化の考え方

全ポリゴンを毎回調べると重いため、最初にSQLiteで次の条件を使います。

```sql
WHERE min_lat <= ?
  AND max_lat >= ?
  AND min_lon <= ?
  AND max_lon >= ?
```

これで「その緯度・経度を含む可能性がある区域」だけになります。その後、正確なポリゴン内判定を行います。

### ポリゴン内判定

`point_in_ring()` は、点から右方向へ線を引いたとき、ポリゴンの辺と交差する回数を数える方式です。交差回数が奇数なら内側、偶数なら外側です。

`point_in_polygon()` は複数リングをeven-odd ruleで扱います。島や穴を含む行政区域でも実用上扱えるようにしています。

## API

| API | 内容 |
|---|---|
| `GET /api/health` | DB有無と行政区域件数を返す |
| `GET /api/latest` | 最後に受信した地名付き位置を返す |
| `GET /api/history` | 直近100件の履歴を返す |
| `POST /api/position` | 緯度・経度を受け取り、地名へ変換する |

## `POST /api/position` の内部処理

`app.py` の `post_position(payload)` が処理します。

1. `lat` と `lon` をfloatへ変換する。
2. 不正な場合はHTTP 400を返す。
3. `geocoder.reverse(lat, lon)` を呼ぶ。
4. 結果に時刻、高度、送信元、チャンネルを付ける。
5. `append_csv(row)` でCSVへ追記する。
6. `latest` と `history` を更新する。
7. JSONで結果を返す。

## CSV保存

`append_csv(row)` が `GEOCODER_OUTPUT_CSV` へ追記します。ファイルが空の場合はヘッダも書きます。

地名が見つからない場合でも、緯度・経度の記録としてCSVへ保存されます。その場合、`prefecture` や `address_label` は空になります。

## 最新地名の保持

`latest` は最後に受信した1件です。`telop_output` は `GET /api/latest` を呼んで、この最新地名を取得します。

`history` はメモリ上の直近100件です。コンテナを再起動するとメモリ上の履歴は消えますが、CSVには残ります。

## ログ

ログはローテーションされ、コンテナ内では次に出ます。

```text
/app/logs/reverse_geocoder.log
```

ホスト側では次です。

```text
reverse_geocoder/logs/reverse_geocoder.log
```

主なログ:

| `flow=` | 意味 |
|---|---|
| `geocoder init` | DB読み込みと件数 |
| `geocoder receive` | GPS位置を受信 |
| `geocoder csv_write` | 地名付きCSVへ追記 |
| `geocoder success` | 地名変換成功 |
| `geocoder not_found` | 該当行政区域なし |
| `geocoder invalid_payload` | `lat` / `lon` がない、または不正 |

## 別サーバ化するときの注意

`reverse_geocoder` を別サーバへ移す場合、`gps_receiver/.env` の送信先を変更します。

```text
REVERSE_GEOCODER_URL=http://<reverse_geocoderサーバ>:8020/api/position
```

`telop_output` が地名を読む場合は、`telop_output/.env` も変更します。

```text
REVERSE_GEOCODER_LATEST_URL=http://<reverse_geocoderサーバ>:8020/api/latest
```

行政区域DBはローカルファイルなので、別サーバ化しても外部APIへの常時依存はありません。ただし初回起動時や更新時は `GEOCODER_DATA_URL` へアクセスします。

