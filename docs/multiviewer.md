# マルチビューア

## 役割

`reverse-geocoder` が逆ジオ結果の住所をマルチビューアへTCP送信します。  
ATEM設定とは独立しています。

```text
GPS fix
↓
reverse-geocoder
↓
address_label
↓
Multiviewer TCP
```

## 主な設定

`reverse_geocoder/.env`:

```env
MULTIVIEWER_ENABLED=1
MULTIVIEWER_HOST=192.168.11.69
MULTIVIEWER_PORT=51069
MULTIVIEWER_COMMAND_PREFIX=STW010V010
MULTIVIEWER_DATETIME_ENABLED=1
MULTIVIEWER_DATETIME_COMMAND_PREFIX=STW010V011
MULTIVIEWER_DATETIME_FORMAT=%m/%d %H:%M:%S
MULTIVIEWER_TEXT_TEMPLATE={address_label}
MULTIVIEWER_ENCODING=shift_jis
MULTIVIEWER_TIMEOUT_SECONDS=2.0
MULTIVIEWER_SEND_ON_NOT_FOUND=0
MULTIVIEWER_DEDUP_TEXT=1
```

地名だけを送る:

```env
MULTIVIEWER_TEXT_TEMPLATE={address_label}
```

逆ジオ失敗時に空文字を送る運用:

```env
MULTIVIEWER_SEND_ON_NOT_FOUND=0
```

## 手動送信

例:

```bash
python3 send_multiviewer.py '大阪府大阪市' --prefix STW010V010
python3 send_multiviewer.py '' --prefix STW010V010
```

日時側:

```bash
python3 send_multiviewer.py '07/18 16:30:00' --prefix STW010V011
```

## 反映

```bash
docker compose up -d --build reverse-geocoder
```

確認:

```bash
curl -s http://127.0.0.1:8020/api/latest | python3 -m json.tool
tail -f reverse_geocoder/logs/reverse_geocoder.log
```

