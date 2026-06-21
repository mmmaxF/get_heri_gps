# Documentation

このフォルダに、`get_heri_gps` の設計・処理・運用ドキュメントをまとめています。

| ドキュメント | 内容 |
|---|---|
| `DATA_FLOW.md` | GPS音声入力から逆ジオ、マルチビューアー送信までの全体フロー |
| `GPS_DEMOD.md` | SDI音声からGPS/MODを復調する方式 |
| `GPS_RECEIVER_DETAILS.md` | `gps_receiver` コンテナと関数の詳細 |
| `REVERSE_GEOCODER_DETAILS.md` | `reverse_geocoder` コンテナと関数の詳細 |
| `REVERSE_GEOCODING_SPEC.md` | 逆ジオコーディングの仕様 |
| `SEND_MULTIVIEWER.md` | マルチビューアーへ手動送信するCLIの使用方法 |

テロップ画像を生成する旧 `telop_output` 構成は廃止済みです。現在は、逆ジオコーダーが生成した地名をマルチビューアーへTCPコマンドで直接送信します。
