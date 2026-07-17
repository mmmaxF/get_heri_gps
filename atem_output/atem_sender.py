#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import time

from PIL import Image
from pyatem.command import (
    DkeyOnairCommand,
    DkeySetFillCommand,
    DkeySetKeyCommand,
    MediaplayerSelectCommand,
    TimeRequestCommand,
)
import pyatem.media
from pyatem.protocol import AtemProtocol


def env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--text", default="")
    parser.add_argument("--clear-display", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    media_pool_slot = env_int("ATEM_MEDIA_POOL_SLOT", 1)
    media_player = env_int("ATEM_MEDIA_PLAYER", 1)
    dsk = env_int("ATEM_DSK", 1)
    fill_source = env_int("ATEM_FILL_SOURCE", 3010)
    key_source = env_int("ATEM_KEY_SOURCE", 3011)
    on_air = env_bool("ATEM_ON_AIR", True)
    compress = env_bool("ATEM_UPLOAD_COMPRESS", False)

    done = False
    connection = AtemProtocol(args.host)
    slot_index = max(0, media_pool_slot - 1)
    player_index = max(0, media_player - 1)
    dsk_index = max(0, dsk - 1)
    output = {
        "enabled": True,
        "sent": False,
        "skipped": False,
        "host": args.host,
        "media_pool_slot": media_pool_slot,
        "media_player": media_player,
        "dsk": dsk,
        "fill_source": fill_source,
        "key_source": key_source,
    }

    def connected():
        mode = connection.mixerstate["video-mode"]
        resolution = mode.get_resolution()
        output["mode"] = mode.get_label()
        output["resolution"] = list(resolution)
        frame = Image.new("RGBA", resolution, (0, 0, 0, 0))
        graphic = Image.open(args.image).convert("RGBA")
        graphic.thumbnail(resolution, Image.Resampling.LANCZOS)
        frame.alpha_composite(graphic, (0, 0))
        frame_atem = pyatem.media.rgb_to_atem(frame.tobytes(), *resolution)
        connection.send_commands([TimeRequestCommand()])
        connection.upload(
            0,
            slot_index,
            frame_atem,
            name=args.text[:31] or "heri_gps",
            compress=compress,
        )

    def uploaded(store, slot):
        nonlocal done
        commands = [
            MediaplayerSelectCommand(player_index, still=slot_index),
            DkeySetFillCommand(dsk_index, fill_source),
            DkeySetKeyCommand(dsk_index, key_source),
            DkeyOnairCommand(dsk_index, bool(on_air and not args.clear_display)),
        ]
        connection.send_commands(commands)
        output["sent"] = True
        done = True

    connection.on("connected", connected)
    connection.on("upload-done", uploaded)
    connection.connect()
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline and not done:
        connection.loop()
    if not done:
        raise TimeoutError("ATEM upload timed out")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
