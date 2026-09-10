# NNN入力

音声キャプチャ設定の「GPS方式」で NNN / MapSystem を選択する。
新規設定の既定値は NNN / CH3 / 4ch入力。保存済みの入力チャンネル設定は優先される。
方式変更時は NNN→CH3、MapSystem→CH4を設定するが、CHは手動変更可能。
実機が対応する入力チャンネル数を指定すること。2ch機器では物理A3をそのまま取得できない。

停止→方式・入力設定を保存→開始の順で切り替える。開始操作も先に設定を保存する。
`TELEMETRY_FORMAT=nnn|mapsystem` はcapture-agent設定に保存され、PCMヘッダーの
`telemetry_format` で受信側へ渡される。ヘッダーに方式がない旧送信元はMapSystem扱い。
方式変更は新しい接続から適用され、PCMバッファ・重複判定は接続ごとに初期化する。

NNNは1200/2200Hz、1200baud。8N1相当の受信で下位7bitを取り出し、
STX + ASCII 22文字 + ETX + XOR BCCの25バイトを検証する。
BCCはSTXを除くペイロードとETXのXOR。
緯度DDMMSS・経度DDDMMSSを度分秒として変換する（分の小数ではない）。
北緯・東経を前提とする。高度は同時収録との比較に基づき10m単位と推定し、mへ変換する。
先頭100・19の意味と高度基準は未確定。group/aircraftには意味を推測して格納しない。
payload_hexは7bit化したSTX～BCC全体。

既存GpsFixを返しCSV・UI・送信キュー・POST /api/positionを共用する。
NNNにはカメラ方向情報がなく、撮影地点推定は利用できない。
既存のE2EボタンはMapSystem疑似信号の検査であり、NNN復調の検査ではない。

検証: `cd gps_receiver && python -m unittest test_nnn_demodulator`
反映にはgps-receiverのイメージ再ビルドとcapture-agent制御プロセスの再起動が必要。
保存済み.envは自動上書きしない。切替時にUIでNNN・実際の入力数・CH3を保存する。
