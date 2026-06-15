#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPS/MOD demodulation utilities for SDI audio channels.

The demodulator expects signed 16-bit PCM samples from a single audio channel.
The discovered signal is 1200 baud FSK/AFSK-like audio with 1200/1800 Hz tones.
After differential decode + invert, frames are HDLC/AX.25-like and carry
`:MOD...` info fields.
"""

from dataclasses import dataclass
import os

import numpy as np


def env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


DEFAULT_SAMPLE_RATE = env_int("SAMPLE_RATE", 48000)
DEFAULT_BAUD = env_int("GPS_BAUD", 1200)
DEFAULT_MARK_HZ = env_int("GPS_MARK_HZ", 1200)
DEFAULT_SPACE_HZ = env_int("GPS_SPACE_HZ", 1800)
HDLC_FLAG = np.asarray([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.uint8)


@dataclass(frozen=True)
class GpsFix:
    sample_offset: int
    phase: float
    group: int | None
    aircraft: int | None
    lat: float
    lon: float
    alt: int
    payload_hex: str


def bcd_byte(value):
    hi = (value >> 4) & 0xF
    lo = value & 0xF
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def parse_u16_bcd(buf):
    if len(buf) != 2:
        return None
    a = bcd_byte(buf[0])
    b = bcd_byte(buf[1])
    if a is None or b is None:
        return None
    return a * 100 + b


def parse_dms_bcd(buf):
    """Parse 5-byte DMS BCD coordinate: DDD MM SS CC."""
    if len(buf) != 5:
        return None
    deg_hi = bcd_byte(buf[0])
    deg_lo = bcd_byte(buf[1])
    minute = bcd_byte(buf[2])
    second = bcd_byte(buf[3])
    centi = bcd_byte(buf[4])
    if None in (deg_hi, deg_lo, minute, second, centi):
        return None
    degree = deg_hi * 100 + deg_lo
    if minute >= 60 or second >= 60:
        return None
    return degree + minute / 60.0 + (second + centi / 100.0) / 3600.0


def parse_mod_info(info):
    """Parse an AX.25 info field beginning with b':MOD'."""
    if not info.startswith(b":MOD") or len(info) < 21:
        return None
    payload = info[1:]
    if not payload.startswith(b"MOD"):
        return None

    group = parse_u16_bcd(payload[3:5])
    aircraft = parse_u16_bcd(payload[5:7])
    lat = parse_dms_bcd(payload[7:12])
    lon = parse_dms_bcd(payload[12:17])
    alt = parse_u16_bcd(payload[17:19])
    if lat is None or lon is None or alt is None:
        return None
    return {
        "group": group,
        "aircraft": aircraft,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "payload_hex": payload.hex(),
    }


def goertzel(blocks, freq, sample_rate=DEFAULT_SAMPLE_RATE):
    n = blocks.shape[1]
    t = np.arange(n, dtype=np.float32) / sample_rate
    osc = np.exp(-2j * np.pi * freq * t).astype(np.complex64)
    values = blocks @ osc
    return values.real * values.real + values.imag * values.imag


def fsk_bits(samples, phase, sample_rate=DEFAULT_SAMPLE_RATE, baud=DEFAULT_BAUD, mark_hz=DEFAULT_MARK_HZ, space_hz=DEFAULT_SPACE_HZ):
    step = int(round(sample_rate / baud))
    start = int(round(phase * step))
    usable = ((len(samples) - start) // step) * step
    if usable < step * 64:
        return None
    blocks = samples[start : start + usable].reshape(-1, step)
    return (goertzel(blocks, space_hz, sample_rate) > goertzel(blocks, mark_hz, sample_rate)).astype(np.uint8)


def diff_bits(bits):
    if bits is None or len(bits) < 2:
        return bits
    return (bits[1:] ^ bits[:-1]).astype(np.uint8)


def find_flags(bits):
    flags = []
    for i in range(0, len(bits) - 7):
        if np.array_equal(bits[i : i + 8], HDLC_FLAG):
            flags.append(i)
    return flags


def unstuff(bits):
    out = []
    ones = 0
    i = 0
    while i < len(bits):
        bit = int(bits[i])
        out.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                if i + 1 < len(bits) and bits[i + 1] == 0:
                    i += 1
                ones = 0
        else:
            ones = 0
        i += 1
    return np.asarray(out, dtype=np.uint8)


def bits_to_bytes_lsb(bits):
    usable = (len(bits) // 8) * 8
    if usable <= 0:
        return b""
    arr = bits[:usable].reshape(-1, 8)
    return np.packbits(arr[:, ::-1].astype(np.uint8), axis=1, bitorder="big")[:, 0].tobytes()


def crc16_x25(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc & 0xFFFF


def decode_hdlc_frames(bits):
    flags = find_flags(bits)
    frames = []
    for start, end in zip(flags, flags[1:]):
        if end - start < 24:
            continue
        payload_bits = unstuff(bits[start + 8 : end])
        frame = bits_to_bytes_lsb(payload_bits)
        if len(frame) >= 18:
            frames.append((start, end, frame))
    return frames


def normalize_samples(samples):
    x = samples.astype(np.float32)
    x -= float(np.mean(x))
    peak = float(np.max(np.abs(x))) or 1.0
    return x / peak


def decode_samples(samples, window_start_sample=0, sample_rate=DEFAULT_SAMPLE_RATE):
    """Return GPS fixes decoded from a single-channel PCM sample array."""
    x = normalize_samples(samples)
    best = []
    for phase in (0.0, 0.25, 0.5, 0.75):
        bits = fsk_bits(x, phase, sample_rate=sample_rate)
        if bits is None:
            continue
        bits = 1 - diff_bits(bits)
        phase_rows = []
        for bit_start, _bit_end, frame in decode_hdlc_frames(bits):
            if len(frame) < 18 or frame[14] != 0x03 or frame[15] != 0xF0:
                continue
            if crc16_x25(frame) != 0xF0B8:
                continue
            parsed = parse_mod_info(frame[16:-2])
            if not parsed:
                continue
            sample_offset = window_start_sample + int(bit_start * sample_rate / DEFAULT_BAUD)
            phase_rows.append(GpsFix(sample_offset=sample_offset, phase=phase, **parsed))
        if len(phase_rows) > len(best):
            best = phase_rows
    return best
