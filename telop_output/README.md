# telop_output

`reverse_geocoder` の最新地名から、V信号/Key信号のテロッププレビューを生成するコンテナです。

初期実装では、実機出力の前段として以下を提供します。

```text
GET  /api/status
GET  /api/output-devices
GET  /api/config
POST /api/config
POST /api/start
POST /api/stop
GET  /api/preview/v.png
GET  /api/preview/key.png
```

設定は `/app/config/telop_config.json` に保存されます。
