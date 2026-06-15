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

復調処理の本体は、このプロジェクト内の以下にあります。

```text
gps_receiver/gps_demodulator.py
```

処理の流れ:

```text
1. 単一音声チャンネルの signed 16-bit little-endian PCM を読む
2. 1200Hz / 1800Hz の強さを1ビット区間ごとに比較する
3. 1200baud のビット列へ戻す
4. differential decode + invert を行う
5. HDLCフラグ 0x7e でフレーム分割する
6. bit stuffing を解除する
7. AX.25風ヘッダと X.25 CRC を検証する
8. info部の :MOD を取り出す
9. MOD内のBCD/DMSから緯度・経度・高度を得る
```

実機SDIなしでRAW/captureディレクトリを復調する場合:

```bash
cd gps_receiver
python demodulate_gps.py ../../audio_capture/20260613_132355 --channel 2 --limit-sec 60 --output output/demodulated_gps.csv
```

Docker内で単体復調する場合は、`gps_receiver/input/` に `ch2.raw` と `metadata.json` を含むcaptureディレクトリ、または単体RAWを置いて実行します。

```bash
docker compose run --rm gps-demodulator
```

任意のパスをマウントして復調する場合:

```bash
docker compose run --rm \
  -v /home/ubuntu/app/hericheck/audio_capture/20260613_132355:/captures/20260613_132355:ro \
  get-heri-gps \
  python demodulate_gps.py /captures/20260613_132355 --channel 2 --limit-sec 60 --output /app/output/demodulated_gps.csv
```

## 起動

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
cd gps_receiver
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

設定値はコンテナごとに分けています。初回の `./start.sh` 実行時に、各 `.env` がなければ `.env.example` から自動作成されます。

```text
.env                         全体起動用のポート、マウントパス
gps_receiver/.env            GPS取得・復調コンテナ用
reverse_geocoder/.env        逆ジオコーダー用
telop_output/.env            テロップ出力用
```

主な設定:

```text
.env:
  GPS_RECEIVER_PORT=8010
  REVERSE_GEOCODER_PORT=8020
  TELOP_OUTPUT_PORT=8030

gps_receiver/.env:
  INPUT_DEVICE=hw:2,0
  INPUT_CHANNELS=2
  GPS_CHANNEL=2
  SAMPLE_RATE=48000
```

実機に合わせて変更する場合は、`docker-compose.yml` ではなく各サービスの `.env` を編集します。

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

コンテナを別サーバへ分離する場合は、対象フォルダだけを配置して、その中の `.env` を編集して起動できます。

```bash
cd gps_receiver
docker compose up -d --build
```

```bash
cd reverse_geocoder
docker compose up -d --build
```

```bash
cd telop_output
docker compose up -d --build
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
./gps_receiver/output/gps_positions.csv
```

実SDI受信中のログは以下に出ます。各ログは容量上限付きでローテーションします。

```text
gps_receiver/logs/gps_receiver.log
reverse_geocoder/logs/reverse_geocoder.log
telop_output/logs/telop_output.log
```

詳しいデータフローとログの見方は `DATA_FLOW.md` を参照してください。

コンテナ内で録音デバイスを確認する場合:

```bash
docker compose exec get-heri-gps arecord -l
```

AJAやHDMI to USBキャプチャが `hw:2,0` 以外で見える場合は、`gps_receiver/.env` の `INPUT_DEVICE` を変更してください。

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

地名データの取得URLを変更する場合は、`reverse_geocoder/.env` の `GEOCODER_DATA_URL` を変更してください。

## テロップ出力

`telop-output` コンテナは、逆ジオコーダーの最新地名からV/Keyプレビューを生成します。

8010画面の `テロップ出力` セクションで以下を設定できます。

```text
V出力
Key出力
出力解像度
フレームレート
ピクセル形式
Key形式
Keyマット濃度
フォント
文字揃え
フォントサイズ
文字色
Vマット色
Vマット濃度
縁取り色
縁取り幅
配置・サイズ
```

V出力とKey出力は未選択でも利用できます。
未選択の場合も、VプレビューとKeyプレビューはブラウザ上で確認できます。
Vプレビュー上の破線枠は配置・サイズ変更用の操作ガイドで、生成されるV/Key画像には入りません。
Keyマット濃度を `0%` にすると、Keyの文字以外の部分は真っ黒になります。

HDMIポートからSDI変換器へ出す場合、OS上では `/dev/video*` ではなく `Display DP-* / HDMI-*` のようなディスプレイ出力として認識されます。
接続済みのディスプレイ出力は、V/Key出力プルダウンに `Display ...` として表示します。

追加フォントを使う場合は、以下に `.ttf` / `.otf` / `.ttc` を置くと、UIのフォントプルダウンに表示されます。
標準では、Noto CJK、IPA/IPAex、Takao、M+ など日本語対応フォントを表示します。

```text
telop_output/assets/fonts/
```

API確認:

```bash
curl http://127.0.0.1:8030/api/status
```

## 実SDI入力

実SDI入力では、UIの `入力デバイス` プルダウンからAJAやHDMI to USBキャプチャなどの録音デバイスを選びます。
アプリは選択デバイスから自動で `arecord` コマンドを生成します。
プルダウンには、OSが認識しているSDI/AJA/Blackmagic系、またはHDMI to USBキャプチャ系の入力デバイスだけを表示します。
対象キーワードを変える場合は、`gps_receiver/.env` の `CAPTURE_DEVICE_INCLUDE_KEYWORDS` を変更してください。

AJA U-TAPでは、以下のように見えます。

```text
AJA U-TAP 709042 / USB Audio (hw:2,0)
```

MS2109系のHDMI to USBキャプチャでは、以下のように見えます。

```text
MS2109 / USB Audio (hw:3,0)
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
gps_receiver/output/gps_positions.csv
```

列:

```text
time,source,channel,offset_sec,lon,lat,alt,group,aircraft,payload_hex
```

`time` は日本時間の以下の形式で出力します。

```text
2026/06/14 02:46:56
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
