#!/usr/bin/env python3
"""Restart this checkout's control API, preserving configuration and run state."""

import argparse
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time

from web_app import read_env


BASE = Path(__file__).resolve().parent


def request(path, endpoint, method="GET"):
    connection = http.client.HTTPConnection("localhost", timeout=8)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(8)
    try:
        connection.sock.connect(str(path))
        peer_pid, _, _ = struct.unpack(
            "3i", connection.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        connection.request(method, endpoint, body=b"" if method == "POST" else None)
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status != 200 or result.get("ok") is False:
            raise RuntimeError(f"{endpoint}: {result}")
        return peer_pid, result
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    env_path = BASE / ".env"
    config = read_env(env_path)
    socket_path = Path(config["CAPTURE_AGENT_CONTROL_SOCKET"])
    if not socket_path.is_absolute():
        socket_path = BASE / socket_path
    resume = False
    try:
        pid, status = request(socket_path, "/api/status")
    except (FileNotFoundError, ConnectionRefusedError):
        pid = None
    if pid is not None:
        # Identify the server through the socket rather than trusting a stale PID file.
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if os.fsencode(str(BASE / "web_app.py")) not in command:
            raise RuntimeError(f"制御ソケットのPID {pid}はこのプロジェクトの制御APIではありません")
        resume = bool(status.get("running"))
        print(f"capture-agent制御APIを再起動します (PID {pid})", flush=True)
        request(socket_path, "/api/stop", "POST")
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            proc = Path(f"/proc/{pid}/stat")
            if not proc.exists():
                break
            try:
                if proc.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            except FileNotFoundError:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("旧制御APIの停止を確認できません")
    else:
        print("capture-agent制御APIを起動します", flush=True)

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    with open(args.log, "ab", buffering=0) as log:
        process = subprocess.Popen(
            ["python3", "-u", str(BASE / "web_app.py"), "--env-file", str(env_path)],
            cwd=BASE, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True,
        )
    (BASE / "run").mkdir(exist_ok=True)
    (BASE / "run/control-api.pid").write_text(str(process.pid) + "\n")
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"制御APIの起動に失敗しました。ログ: {args.log}")
        try:
            new_pid, _ = request(socket_path, "/api/status")
            if new_pid == process.pid:
                break
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError(f"制御APIの応答を確認できません。ログ: {args.log}")
    if resume:
        request(socket_path, "/api/start", "POST")
        print("音声送信を再開しました（受信側起動まで自動再接続します）", flush=True)
    print(f"capture-agent制御API 起動確認済み (PID {process.pid})", flush=True)


if __name__ == "__main__":
    main()
