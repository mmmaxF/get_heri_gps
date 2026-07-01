# capture-agent

Dockerの外（ホストOS）でSDIキャプチャ機器の音声をPCM化し、
TCP Socketで`gps-receiver`コンテナへ連続送信するエージェントです。
Pythonの外部パッケージは不要です。

## データの流れ

```text
SDIキャプチャ機器
  -> arecord等のキャプチャコマンド
  -> S16_LE / 48kHz / interleaved raw PCM
  -> capture-agent
  -> TCP :9010
  -> gps-receiver
```

これはHTTP APIではなく、連続したバイナリ音声に向くTCP Socket通信です。
接続直後に1行のJSONヘッダーを送り、その後はraw PCMだけを送ります。

## 制御APIを起動

```bash
cd /home/ubuntu/app/hericheck/get_heri_gps/capture_agent
./start.sh
```

`capture-agent`自身は画面を提供しません。設定と開始・停止は、Docker側の
GPS受信UIに統合されています。

```text
http://<ホストのIPアドレス>:8010/
```

GPS受信UIで音声入力デバイス、入力チャンネル数、GPS音声チャンネル、送信先を
設定できます。入力チャンネル数の初期値は4ch、GPS音声チャンネルの
初期値はCH4です。「送信開始」を押すとPCMのキャプチャと送信が始まります。

同一ホスト上のDockerへ送る場合は送信先を`127.0.0.1:9010`にします。
別マシンへ送る場合はDockerホストのIPアドレスを指定します。

UIを使わず従来どおり実行する場合は、次のようにします。

```bash
./start.sh --headless
./start.sh --headless --list-devices
./start.sh --headless --check
```

## AJAなど別のキャプチャ方法

キャプチャ方法は`CAPTURE_COMMAND`で差し替えられます。コマンドは音声を
`S16_LE`、48kHz、設定したチャンネル数のinterleaved raw PCMとして
標準出力へ出してください。

```env
CAPTURE_COMMAND=任意のキャプチャコマンド
```

コマンドの音声形式と`SAMPLE_RATE`、`SAMPLE_FORMAT`、`INPUT_CHANNELS`が
一致している必要があります。

## 送信ヘッダー

接続ごとに次の形式のJSONと改行を送ります。

```json
{
  "protocol": "heri-pcm",
  "version": 1,
  "sample_rate": 48000,
  "sample_format": "S16_LE",
  "channels": 4,
  "gps_channel": 4,
  "agent_name": "sdi-capture-01"
}
```

`gps-receiver`は、このヘッダーのチャンネル数とGPSチャンネルに従って
受信PCMから対象チャンネルを抽出します。
