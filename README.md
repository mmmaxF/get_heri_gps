# get_heri_gps

SDI入力の音声チャンネルからGPS/MODデータを復調し、位置情報CSVを更新し続けるWebアプリです。

今回の解析で判明した方式に合わせ、デフォルトでは `ch2` をGPSチャンネルとして扱います。

## 機能

- SDI 1入力を想定
- UI上でGPS入り音声チャンネルを選択
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

## 実SDI入力

実SDI入力では、PCMをstdoutへ出すコマンドを指定します。

例:

```bash
INPUT_COMMAND='arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -t raw' python app.py
```

UIで `実機入力コマンド` に上記のようなコマンドを入れて Start を押します。

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
