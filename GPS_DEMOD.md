# GPS Demodulation Summary

作成日: 2026-06-13

このファイルは、`audio_capture` に含まれるGPS音声データの解析で判明したことを、結論ベースでまとめたものです。
`get_heri_gps` だけをpushしても復調方式が分かるように、このファイル内で要点を完結させています。

## 1. 結論

`audio_capture` の音声データからGPS座標を復調できました。

最終的な結論は以下です。

```text
audio_capture/*/ch2.raw に、GPS/MODデータが入っている。
方式は 1200baud のFSK/AFSK相当。
周波数は 1200Hz / 1800Hz。
ビット列は differential decode 後に反転すると読める。
フレームは HDLC/AX.25風。
AX.25 info部に :MOD で始まるペイロードが入っている。
:MOD の中身は NnnMap がCOMポートから受けていたMODバイナリ形式と一致する。
MODペイロード内のBCD/DMSフィールドから緯度・経度・高度を取り出せる。
```

今日の `audio_capture` 全体から、GPSレコード4742件を抽出しました。

出力ファイル:

```text
mod_gps_today.csv
```

## 2. 重要なファイル

### 入力データ

```text
audio_capture/
```

今回の成功解析では、ユーザー指定どおり以下は参照していません。

```text
audio_capture_mbs/
audio_capture_ktv/
```

### 正解比較データ

```text
correctData/trk10.20260613-132404.csv
correctData/trk10.20260613-135355.csv
```

`correctData` の時刻はUTCです。
`audio_capture/*/metadata.json` の時刻はJSTです。

### get_heri_gps内の復調実装

```text
gps_demodulator.py
```

リアルタイムアプリとCLIが共通で使う復調ロジックです。

```text
demodulate_gps.py
```

実機SDIなしで、過去の `audio_capture/*/ch2.raw` や単体RAWファイルからGPS CSVを作るCLIです。

```text
app.py
```

Web UI/APIからSDI音声を受け、`gps_demodulator.py` を呼んでリアルタイムにGPSを抽出します。

### 出力CSV

```text
mod_gps_today.csv
```

今日の `audio_capture` から抽出したGPS座標CSVです。

列:

```text
time,capture,offset_sec,lon,lat,alt,group,aircraft,phase,variant,payload_hex
```

## 3. 復調条件

最終的にGPSが取れた条件は以下です。

```text
対象チャンネル : ch2.raw
サンプル形式   : 48000Hz / signed 16bit little endian PCM
ボーレート     : 1200 baud
FSK周波数      : 1200Hz / 1800Hz
ビット処理     : differential decode + invert
フレーム       : HDLC/AX.25風
フラグ         : 0x7e
CRC            : X.25 CRC
AX.25宛先      : CQ
AX.25送信元    : MIKE
control        : 0x03
PID            : 0xf0
info           : :MOD...
```

`ch1` ではなく、`ch2` から安定してGPS/MODフレームが取れました。

## 4. フレーム構造

復調後のフレームは、AX.25/HDLC風に見えます。

確認できた主な構造:

```text
HDLC flag 0x7e
AX.25 destination = CQ
AX.25 source      = MIKE
control           = 0x03
PID               = 0xf0
info              = :MOD...
FCS               = X.25 CRC
HDLC flag 0x7e
```

CRC OKになるため、単なる偶然の文字列ではなく、正しく復調できていると判断できます。

## 5. MODペイロード

AX.25のinfo部は `:MOD` で始まります。

先頭の `:` を除くと、NnnMapがCOMポートから受けていたと考えられる `MOD` バイナリペイロードになります。

```text
info    = :MOD...
payload = MOD...
```

MODペイロード長は49バイトです。
これは、NnnMap.exe解析で見つかったMODバイナリ長と一致します。

## 6. 座標フィールド

MODペイロード先頭付近の構造は以下です。

```text
payload[0:3]   = "MOD"
payload[3:5]   = group, BCD 2バイト
payload[5:7]   = aircraft, BCD 2バイト
payload[7:12]  = latitude, DMS BCD 5バイト
payload[12:17] = longitude, DMS BCD 5バイト
payload[17:19] = altitude, BCD 2バイト
```

緯度・経度の5バイトは以下の形式です。

```text
DDD MM SS CC
```

意味:

```text
DDD = 度
MM  = 分
SS  = 秒
CC  = 1/100秒
```

10進度への変換式:

```text
decimal_degree = degree + minute / 60 + (second + centisecond / 100) / 3600
```

例:

```text
lon bytes = 01 35 34 34 64
=> 135度34分34.64秒
=> 135.57628889度

lat bytes = 00 34 35 46 15
=> 34度35分46.15秒
=> 34.59615278度
```

高度はBCD 2バイトです。

例:

```text
alt bytes = 02 81
=> 281
```

## 7. 抽出結果

実行したコマンド:

```bash
.venv/bin/python extract_mod_gps.py \
  audio_capture/20260613_132355 \
  audio_capture/20260613_133355 \
  audio_capture/20260613_134355 \
  audio_capture/20260613_135356 \
  audio_capture/20260613_140356 \
  audio_capture/20260613_141357 \
  audio_capture/20260613_142357 \
  audio_capture/20260613_143358 \
  audio_capture/20260613_145344 \
  --limit-sec 600 \
  --fast \
  --output mod_gps_today.csv
```

出力:

```text
mod_gps_today.csv
```

行数:

```text
ヘッダ込み 4743行
GPSレコード 4742件
```

セグメント別抽出件数:

```text
audio_capture/20260613_132355 : 576件
audio_capture/20260613_133355 : 564件
audio_capture/20260613_134355 : 579件
audio_capture/20260613_135356 : 577件
audio_capture/20260613_140356 : 569件
audio_capture/20260613_141357 : 561件
audio_capture/20260613_142357 : 574件
audio_capture/20260613_143358 : 277件
audio_capture/20260613_145344 : 465件
```

先頭付近の例:

```text
2026-06-13T13:23:57.944500+09:00,
audio_capture/20260613_132355,
lon=135.57629722,
lat=34.59295556,
alt=258
```

末尾付近の例:

```text
2026-06-13T15:01:48.675500+09:00,
audio_capture/20260613_145344,
lon=135.47268056,
lat=34.68369167,
alt=683
```

## 8. correctDataとの照合

`correctData` は、同じSDIから別機器で正しくGPS復号したデータです。

### 13:24側

対象:

```text
correctData/trk10.20260613-132404.csv
audio_capture/20260613_132355
```

代表例:

```text
correct:
2026-06-13T13:24:04+09:00
lon=135.574025
lat=34.591708
alt=281

decoded:
2026-06-13T13:24:04頃
lon=135.574025
lat=34.591708
alt=281

距離差: 約0.04m
```

次の点もほぼ完全一致しました。

```text
13:24:10 距離差 約0.03m / 高度一致
13:24:16 距離差 約0.02m / 高度一致
```

13:24側は、復調結果が正解データとほぼ完全に一致すると言えます。

### 13:54側

対象:

```text
correctData/trk10.20260613-135355.csv
audio_capture/20260613_135356
```

代表例:

```text
correct:
2026-06-13T13:54:01+09:00
lon=135.165011
lat=34.302464
alt=1025

decoded:
2026-06-13T13:54:01頃
lon=135.165147
lat=34.302556
alt=1025

距離差: 約16.1m
```

高度は一致し、座標も同じ軌跡を追っています。
13:24側ほど完全一致しない点はありますが、復調方式と座標解釈は正しいと判断できます。

## 9. NnnMap.exe解析で分かったこと

`NnnMap.exe` は .NET アプリでした。

重要な点:

```text
NnnMap自体はSDI音声を直接復調していない可能性が高い。
System.IO.Ports.SerialPort を使ってCOMポートからデータを受けている。
MOD / ANC 形式のデータを扱うクラスがある。
MODバイナリは47または49バイト。
ANCバイナリは95または97バイト。
```

設定ファイルから分かったCOMポート:

```text
FpuMapSerial1 Ancillary COM11 9600bps / Modem COM12 4800bps
FpuMapSerial2 Ancillary COM13 9600bps / Modem COM14 4800bps
FpuMapSerial3 Ancillary COM15 9600bps / Modem COM16 4800bps
VhfMapSerialReceiver COM17 9600bps
```

今回 `audio_capture/ch2.raw` から取れた `:MOD` ペイロードは、
NnnMapがCOMポートから受けていたMODデータと同系統の内容だと考えられます。

## 10. 以前の13バイト候補について

解析初期には、13バイト程度の候補パケットが見えていました。

しかし、最終的にHDLC/AX.25風の正しいフレームが取れたため、13バイト候補は本命GPSデータではなく、ビット列を誤った境界で切った副産物だった可能性が高いです。

理由:

```text
正しい復調ではHDLCフラグ 0x7e が見える
bit stuffing解除後にAX.25アドレスが読める
X.25 CRCがOKになる
info部に :MOD が出る
:MOD ペイロード長がNnnMapのMOD形式と一致する
correctDataと座標が一致する
```

## 11. 再実行方法

今日のデータを再抽出する場合:

```bash
.venv/bin/python extract_mod_gps.py audio_capture/20260613_* --limit-sec 600 --fast --output mod_gps_today.csv
```

構文チェック:

```bash
python3 -m py_compile extract_mod_gps.py ax25_mod_probe.py mod_anc_header_scan.py
```

CSV確認:

```bash
wc -l mod_gps_today.csv
head -5 mod_gps_today.csv
tail -5 mod_gps_today.csv
```

## 12. get_heri_gpsでの実装状態

現在の `get_heri_gps` では、復調処理は以下のように組み込まれています。

```text
gps_demodulator.py : 復調本体
app.py             : SDI/ALSA入力からリアルタイム復調
demodulate_gps.py  : captureディレクトリまたはRAWファイルのオフライン復調
GPS_DEMOD.md       : 復調方式の説明
README.md          : 起動・操作・CLI利用方法
```

オフライン復調の例:

```bash
python demodulate_gps.py ../audio_capture/20260613_132355 \
  --channel 2 \
  --limit-sec 60 \
  --output output/demodulated_gps.csv
```

## 13. 最終まとめ

このプロジェクトで判明した最重要点は以下です。

```text
GPSは audio_capture の ch2 に入っている。
復調方式は 1200baud / 1200Hz・1800Hz FSK。
差分復号して反転するとHDLC/AX.25風フレームになる。
AX.25 info部の :MOD がGPS本体。
MOD内のBCD/DMSから緯度・経度・高度を得られる。
correctDataと照合して、座標が正しいことを確認済み。
今日のデータは mod_gps_today.csv として抽出済み。
```
