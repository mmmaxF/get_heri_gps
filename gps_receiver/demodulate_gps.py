#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decode GPS/MOD frames from a raw audio channel or capture directory."""

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from gps_demodulator import DEFAULT_SAMPLE_RATE, decode_samples


JST = timezone(timedelta(hours=9))
CSV_HEADER = ["time", "source", "channel", "offset_sec", "lon", "lat", "alt", "group", "aircraft", "payload_hex"]


def format_japanese_time(dt):
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def read_start_time(capture_dir):
    meta_path = Path(capture_dir) / "metadata.json"
    if not meta_path.exists():
        return datetime.now(JST)
    with meta_path.open(encoding="utf-8") as f:
        meta = json.load(f)
    return datetime.fromisoformat(meta["start_time"])


def input_raw_path(path, channel):
    path = Path(path)
    if path.is_dir():
        return path / f"ch{channel}.raw", read_start_time(path), str(path)
    return path, datetime.now(JST), str(path)


def read_raw(path, limit_sec, sample_rate):
    count = None if limit_sec is None else int(float(limit_sec) * sample_rate)
    with Path(path).open("rb") as f:
        data = f.read() if count is None else f.read(count * 2)
    return np.frombuffer(data, dtype="<i2").copy()


def decode_input(path, channel, sample_rate, limit_sec):
    raw_path, start_time, source = input_raw_path(path, channel)
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    samples = read_raw(raw_path, limit_sec, sample_rate)
    rows = []
    for fix in decode_samples(samples, 0, sample_rate=sample_rate):
        offset_sec = fix.sample_offset / sample_rate
        t = start_time + timedelta(seconds=offset_sec)
        rows.append(
            {
                "time": format_japanese_time(t),
                "source": source,
                "channel": channel,
                "offset_sec": f"{offset_sec:.6f}",
                "lon": f"{fix.lon:.8f}",
                "lat": f"{fix.lat:.8f}",
                "alt": fix.alt,
                "group": fix.group,
                "aircraft": "" if fix.aircraft is None else fix.aircraft,
                "payload_hex": fix.payload_hex,
            }
        )
    return rows


def write_csv(rows, output):
    with Path(output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([row.get(key, "") for key in CSV_HEADER])


def main():
    parser = argparse.ArgumentParser(description="Decode GPS/MOD from SDI audio raw captures")
    parser.add_argument("inputs", nargs="+", help="capture directories or single-channel .raw files")
    parser.add_argument("--channel", type=int, default=2, help="channel number when input is a capture directory")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--limit-sec", type=float, default=None)
    parser.add_argument("--output", default="output/demodulated_gps.csv")
    args = parser.parse_args()

    all_rows = []
    for path in args.inputs:
        rows = decode_input(path, args.channel, args.sample_rate, args.limit_sec)
        print(f"{path}: {len(rows)} rows")
        for row in rows[:5]:
            print(f"  {row['time']} lon={row['lon']} lat={row['lat']} alt={row['alt']}")
        all_rows.extend(rows)

    write_csv(all_rows, args.output)
    print(f"wrote {args.output} rows={len(all_rows)}")


if __name__ == "__main__":
    main()
