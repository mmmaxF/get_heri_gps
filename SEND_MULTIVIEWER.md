# send_multiviewer.py

`send_multiviewer.py` は、ターミナルからマルチビューアーへ文字列コマンドを手動送信するためのPython CLIです。

逆ジオコーダーやDockerコンテナを経由せず、このサーバから指定IPアドレスとTCPポートへ直接接続します。

## 基本的な使い方

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps
python3 send_multiviewer.py 大阪府大阪市
```

デフォルトの送信内容:

```text
送信先: 192.168.11.69:51069
文字コード: Shift_JIS
コマンド: STW010V010大阪府大阪市\r\n
```

## 引数

| 引数 | 必須 | デフォルト | 内容 |
|---|---|---|---|
| `text` | 必須 | なし | 送信する地名や文字列 |
| `--host` | 任意 | `192.168.11.69` | マルチビューアーのIPアドレスまたはホスト名 |
| `--port` | 任意 | `51069` | TCPポート番号 |
| `--prefix` | 任意 | `STW010V010` | `text` の前に付けるコマンド文字列 |
| `--encoding` | 任意 | `shift_jis` | 送受信に使用する文字コード |
| `--timeout` | 任意 | `5.0` | TCP接続と応答待ちのタイムアウト秒数 |
| `--raw` | 任意 | 無効 | `text` を完全なコマンドとして送る |

ヘルプ表示:

```bash
python3 send_multiviewer.py --help
```

## 実行例

大阪市を送る:

```bash
python3 send_multiviewer.py 大阪府大阪市
```

送信先を明示する:

```bash
python3 send_multiviewer.py 大阪府大阪市 \
  --host 192.168.11.69 \
  --port 51069
```

完全なコマンドを直接送る:

```bash
python3 send_multiviewer.py STW010V010LIVE --raw
```

`--raw` を使わない場合は、`--prefix` の値が自動で先頭に付きます。どちらの場合も末尾にはCRLFが追加されます。

## 設定の優先順位

```text
1. コマンドライン引数
2. シェルの環境変数
3. reverse_geocoder/.env
4. スクリプト内のデフォルト値
```

対象の環境変数:

```text
MULTIVIEWER_HOST
MULTIVIEWER_PORT
MULTIVIEWER_COMMAND_PREFIX
MULTIVIEWER_ENCODING
MULTIVIEWER_TIMEOUT_SECONDS
```

## 正常時の表示

```text
connect: 192.168.11.69:51069
sent: 'STW010V010大阪府大阪市' + CRLF
response: OK
```

`response: OK` は、TCP接続、コマンド送信、マルチビューアーからの応答受信まで成功したことを示します。

## 主なエラー

### `No route to host`

サーバからマルチビューアーまでのネットワーク経路がありません。機器の電源、LAN、IPセグメント、VLAN、ルーターを確認します。

### `Connection refused`

対象IPには到達していますが、指定ポートでTCP待受していません。ポート番号とマルチビューアーの外部制御機能を確認します。

### `timed out`

TCP接続または応答受信がタイムアウトしました。IP、ポート、LAN接続を確認し、必要なら `--timeout` を増やします。

```bash
python3 send_multiviewer.py 大阪府大阪市 --timeout 10
```

### 文字化けする

デフォルトはShift_JISです。機器仕様に応じて変更できます。

```bash
python3 send_multiviewer.py 大阪府大阪市 --encoding cp932
```

## プログラム内部の流れ

```text
引数を読む
main()
↓
reverse_geocoder/.envを読む
load_env_file()
↓
設定値を決める
config_value()
↓
prefix + text + CRLFを作る
send_command()
↓
指定文字コードでバイト列へ変換する
send_command()
↓
TCP接続してsendall()で送る
send_command()
↓
最大1024バイトの応答を受信する
send_command()
↓
送信内容と応答を表示する
main()
```

## 自動送信との違い

| 項目 | `send_multiviewer.py` | `reverse_geocoder/multiviewer.py` |
|---|---|---|
| 起動 | ターミナルから手動 | `/api/position` 処理中に自動 |
| 入力 | コマンドラインの文字列 | 逆ジオで作った地名 |
| 逆ジオ処理 | しない | する |
| CSV保存 | しない | `app.py` が保存 |
| 重複抑止 | しない | 設定により行う |
| Docker依存 | なし | コンテナ内で動く |

同じ文字列を強制的に再送したい場合は、このCLIを使用します。
