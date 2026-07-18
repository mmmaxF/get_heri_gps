# API

認証はありません。ローカル運用前提です。

## Base URL

| Service | URL | 用途 |
|---|---|---|
| get-heri-gps | `http://<host>:8010` | UI、GPS状態、capture-agent proxy |
| reverse-geocoder | `http://<host>:8020` | 逆ジオ、最新住所 |
| atem-output | `http://<host>:8030` | ATEM PNG状態、プレビュー |

## よく使うAPI

GPS状態:

```bash
curl -s http://127.0.0.1:8010/api/status | python3 -m json.tool
```

逆ジオ最新:

```bash
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
```

ATEM状態:

```bash
curl -s http://127.0.0.1:8030/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8030/api/latest | python3 -m json.tool
```

ATEM PNG:

```bash
curl -s http://127.0.0.1:8030/api/preview.png -o /tmp/atem_preview.png
```

## get-heri-gps

| Method | Path | 内容 |
|---|---|---|
| GET | `/` | UI |
| GET | `/api/status` | GPS受信・復調状態 |
| GET | `/api/system/status` | GPS、逆ジオ、ATEMの統合状態 |
| GET | `/api/capture-agent/status` | capture-agent状態 |
| POST | `/api/capture-agent/start` | capture-agent開始 |
| POST | `/api/capture-agent/stop` | capture-agent停止 |
| GET | `/api/download` | GPS CSV取得 |
| WS | `/ws` | 状態配信 |

## reverse-geocoder

| Method | Path | 内容 |
|---|---|---|
| GET | `/api/health` | DB状態 |
| GET | `/api/latest` | 最新住所・出力結果 |
| GET | `/api/history` | 直近履歴 |
| POST | `/api/position` | 緯度経度を受けて住所化 |

手動POST例:

```bash
curl -s -X POST http://127.0.0.1:8020/api/position \
  -H 'Content-Type: application/json' \
  -d '{"time":"2026/07/18 16:30:00","lat":34.6937,"lon":135.5023,"alt":1000}' \
  | python3 -m json.tool
```

## atem-output

| Method | Path | 内容 |
|---|---|---|
| GET | `/api/health` | ATEM出力状態、worker状態 |
| GET | `/api/latest` | 最新PNG/送信結果 |
| GET | `/api/preview.png` | 最新PNG |
| POST | `/api/position` | ATEM用PNG生成・送信queue投入 |
| POST | `/api/test` | テスト文字列でPNG生成 |

テスト:

```bash
curl -s -X POST http://127.0.0.1:8030/api/test \
  -H 'Content-Type: application/json' \
  -d '{"text":"大阪府大阪市"}' \
  | python3 -m json.tool
```

