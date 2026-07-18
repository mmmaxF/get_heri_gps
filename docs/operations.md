# 運用手順

## よく使うコマンド

作業場所:

```bash
cd /home/vega/app/get_heri_gps
```

状態確認:

```bash
docker compose ps
curl -s http://127.0.0.1:8010/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
curl -s http://127.0.0.1:8030/api/health | python3 -m json.tool
```

ログ確認:

```bash
tail -f gps_receiver/logs/gps_receiver.log
tail -f reverse_geocoder/logs/reverse_geocoder.log
tail -f atem_output/logs/atem_output.log
```

## 起動・再起動

全体を再ビルドして起動:

```bash
docker compose up -d --build
```

特定サービスだけ反映:

```bash
docker compose up -d --build get-heri-gps
docker compose up -d --build reverse-geocoder
docker compose up -d --build atem-output
```

`.env` だけを反映する場合も、対象サービスを再作成します。

```bash
docker compose up -d --force-recreate atem-output
```

停止:

```bash
docker compose down
```

## 主な `.env`

| ファイル | 役割 |
|---|---|
| `.env` | Compose全体、ポート、bind mount |
| `gps_receiver/.env` | GPS/SDI音声入力、復調、逆ジオ送信 |
| `reverse_geocoder/.env` | 逆ジオ、マルチビューア、ATEMへ送る文字列 |
| `atem_output/.env` | ATEM PNG描画、ATEM本体接続 |
| `capture_agent/.env` | DeckLink/SDIキャプチャ |

## 変更後の反映目安

| 変更内容 | 再起動対象 |
|---|---|
| SDI/DeckLink入力 | capture-agent と get-heri-gps |
| GPS復調設定 | get-heri-gps |
| マルチビューア設定 | reverse-geocoder |
| ATEM文字列テンプレート | reverse-geocoder と atem-output |
| ATEM位置・フォント・IP | atem-output |
| 撮影位置計算 | reverse-geocoder |

## 現在の表示状態を見る

GPS受信側:

```bash
curl -s http://127.0.0.1:8010/api/status | python3 -m json.tool
```

逆ジオ側:

```bash
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
```

ATEM側:

```bash
curl -s http://127.0.0.1:8030/api/latest | python3 -m json.tool
curl -s http://127.0.0.1:8030/api/preview.png -o /tmp/atem_preview.png
```

