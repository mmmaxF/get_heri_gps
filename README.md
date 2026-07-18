# get_heri_gps

SDI/HDMIキャプチャの音声チャンネルからGPS/MOD信号を復調し、位置をCSVへ保存、ローカル行政区域DBで地名化してマルチビューアーへ送信するDocker Composeプロジェクトです。

## 最短起動

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
./start.sh
```

UI: `http://<サーバIP>:8010/`

## ドキュメント

- [ドキュメント入口](docs/README.md)
- [運用手順](docs/operations.md)
- [トラブルシュート](docs/troubleshooting.md)
- [アーキテクチャ](docs/architecture.md)
- [ATEMスーパー](docs/atem.md)
- [マルチビューア](docs/multiviewer.md)
- [API](docs/api.md)
