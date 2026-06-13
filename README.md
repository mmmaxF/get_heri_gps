# get_heri_gps

SDI入力の音声チャンネルからGPS/MODデータを復調し、位置情報CSVを更新し続けるWebアプリです。

今回の解析で判明した方式に合わせ、デフォルトでは `ch2` をGPSチャンネルとして扱います。

## 機能

- SDI 1入力を想定
- UI上で録音デバイスとGPS入り音声チャンネルを選択
- 選択チャンネルからGPSを復調
- CSVを継続更新
- UIで最新位置、受信件数、直近ログを確認
- 実機SDI入力専用の操作画面

## 復調方式

```text
1200baud FSK/AFSK相当
1200Hz / 1800Hz
differential decode + invert
HDLC/AX.25風フレーム
info = :MOD...
MOD内のBCD/DMSから緯度・経度・高度を抽出
```

## 起動

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

ブラウザ:

```text
http://127.0.0.1:8010
```

## Dockerで起動

Docker環境では、AJAなどのALSA録音デバイスをコンテナへ渡すために `/dev/snd` をマウントします。

簡単起動:

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
./start.sh
```

手動で起動する場合:

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
docker compose up --build
```

バックグラウンドで起動する場合:

```bash
docker compose up -d --build
```

ブラウザ:

```text
http://127.0.0.1:8010
```

別PCから見る場合:

```text
http://<UbuntuのIP>:8010
```

停止:

```bash
docker compose down
```

CSVはホスト側の以下に保存されます。

```text
./output/gps_positions.csv
```

コンテナ内で録音デバイスを確認する場合:

```bash
docker compose exec get-heri-gps arecord -l
```

AJAが `hw:2,0` 以外で見える場合は、`docker-compose.yml` の `INPUT_DEVICE` を変更してください。

## 逆ジオコーディング

`reverse-geocoder` コンテナは、`get_heri_gps` からPOSTされた緯度・経度を都道府県・市区町村に変換します。

デフォルトでは、起動時に国土数値情報 N03 行政区域データの近畿地方版を取得し、SQLite DBを作成します。

```text
reverse_geocoder/data/admin_area.sqlite
```

変換結果CSV:

```text
reverse_geocoder/output/geocoded_positions.csv
```

API確認:

```bash
curl http://127.0.0.1:8020/api/health
```

地名データの取得URLを変更する場合は、`docker-compose.yml` の `GEOCODER_DATA_URL` を変更してください。

## 実SDI入力

実SDI入力では、UIの `入力デバイス` プルダウンからAJAなどの録音デバイスを選びます。
アプリは選択デバイスから自動で `arecord` コマンドを生成します。

今回のAJA U-TAPでは、以下のように見えます。

```text
AJA U-TAP 709042 / USB Audio (hw:2,0)
```

通常の操作:

```text
入力デバイス       : AJA U-TAP 709042 / USB Audio (hw:2,0)
GPS音声チャンネル  : CH2
入力チャンネル数   : 2
```

詳細設定には、自動生成された入力コマンドが表示されます。

```bash
arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -t raw
```

Blackmagic/AJA等で `ffmpeg` から音声PCMを取り出す場合も、最終的に以下の形式でstdoutへ出せば使えます。

```text
signed 16-bit little-endian PCM
48000Hz
interleaved channels
```

## CSV

デフォルト出力:

```text
output/gps_positions.csv
```

列:

```text
time,source,channel,offset_sec,lon,lat,alt,group,aircraft,payload_hex
```

## API

```text
GET  /api/status
POST /api/start
POST /api/stop
POST /api/config
GET  /api/download
WS   /ws
```
