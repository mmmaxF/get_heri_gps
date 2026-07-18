# トラブルシュート

## まず見る

```bash
docker compose ps
curl -s http://127.0.0.1:8010/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
curl -s http://127.0.0.1:8030/api/health | python3 -m json.tool
```

ログ:

```bash
tail -f gps_receiver/logs/gps_receiver.log
tail -f reverse_geocoder/logs/reverse_geocoder.log
tail -f atem_output/logs/atem_output.log
```

## ATEMOUTPUTに接続できない

確認:

```bash
docker compose ps atem-output
curl -sv --max-time 3 http://127.0.0.1:8030/api/health
docker compose exec -T reverse-geocoder python - <<'PY'
import urllib.request, time
url="http://atem-output:8030/api/health"
t=time.time()
try:
    with urllib.request.urlopen(url, timeout=3) as r:
        print(r.status, round(time.time()-t, 3), r.read(200))
except Exception as e:
    print(type(e).__name__, round(time.time()-t, 3), e)
PY
```

見るポイント:

| 状態 | 原因候補 |
|---|---|
| hostから8030が開かない | `atem-output`停止、port bind失敗 |
| reverse-geocoderからだけ失敗 | Docker network、service名 |
| healthが遅い | ATEM送信詰まり、worker状態確認 |

現在は `ATEM_ASYNC_UPLOAD=1` なので、ATEM送信が重くてもAPIは詰まりにくい構成です。

## ATEMにスーパーされない

確認:

```bash
curl -s http://127.0.0.1:8030/api/latest | python3 -m json.tool
tail -80 atem_output/logs/atem_output.log
```

見るポイント:

| 項目 | 意味 |
|---|---|
| `atem_enabled` | `ATEM_ENABLED=1` か |
| `atem_sent` | ATEM送信成功か |
| `clear_display` | trueなら透明PNG/DSK OFF |
| `text` | 実際に送った文字列 |
| `upload_worker` | pending/activeが詰まっていないか |

ログに出ることがあるもの:

```text
file-transfer-error status=no-lock
retransmission detected
```

ATEM Media Pool転送が不安定、またはATEM側がロックを取れない状態です。頻発する場合は更新間隔を長くします。

## 地名が出ない

確認:

```bash
curl -s http://127.0.0.1:8010/api/status | python3 -m json.tool
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
```

見るポイント:

| 項目 | 意味 |
|---|---|
| `latest.lat/lon` | GPS復調できているか |
| `latest_geocode.ok` | 逆ジオ成功か |
| `error: area not found` | 海上・区域外・DB未該当 |
| `clear_display=true` | 表示クリア対象 |

海上や行政区域DB外では住所が空になります。

## 撮影位置不明になる

撮影位置は仮解析したカメラ方位・チルトから地表交点を計算し、その地点を逆ジオしています。

```text
ヘリ位置 + 高度 + カメラ方位 + チルト
↓
撮影地点候補
↓
逆ジオ
```

`capture_address_label` が空なら、以下が考えられます。

- 撮影先が海上・区域外
- カメラ方位/チルトのバイト推定がまだ違う
- チルト解釈が違い、投影距離がズレている
- 行政区域DBに該当ポリゴンがない

確認:

```bash
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
```

見る項目:

```text
capture_lat
capture_lon
capture_distance_m
capture_heading
capture_tilt
capture_error
```

## capture-agent制御APIに接続できない

確認:

```bash
ls -l capture_agent/run
curl -s http://127.0.0.1:8010/api/capture-agent/status | python3 -m json.tool
```

`Connection refused` の場合、capture-agent側が起動していないか、Unix socketが待受していません。

## 1秒更新が重い

ATEM更新はテキスト差し替えではなくMedia Poolアップロードです。

```text
PNG生成
↓
Media Pool upload
↓
Media Player / DSK制御
```

安全運用は5秒、攻めるなら3秒、1秒は短時間テスト向けです。設定は `reverse_geocoder/.env`:

```env
ATEM_MIN_UPDATE_SECONDS=3.0
```

反映:

```bash
docker compose up -d --build reverse-geocoder
```

