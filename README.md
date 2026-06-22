# get_heri_gps

SDI/HDMIキャプチャの音声チャンネルからGPS/MOD信号を復調し、位置をCSVへ保存、ローカル行政区域DBで地名化してマルチビューアーへ送信するDocker Composeプロジェクトです。

## 最短起動

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
./start.sh
```

UI: `http://<サーバIP>:8010/`

## ドキュメント

- [Docker構成](docs/docker.md)
- [アーキテクチャ](docs/architecture.md)
- [非同期連携・再送設計（未実装）](docs/reliable-pipeline-design.md)
- [ワークフロー](docs/workflows.md)
- [API索引](docs/api.md)
- [DB定義](docs/database.md)
- [マルチビューア連携仕様](docs/multiviewer.md)
- [コンテナ別ドキュメント](docs/containers/)
