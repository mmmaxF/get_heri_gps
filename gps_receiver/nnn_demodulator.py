"""NNN Bell-202 UART telemetry; return the common GPS fix representation."""

import re
import numpy as np
from gps_demodulator import GpsFix

PATTERN = re.compile(rb"\d{3} \d{2}(\d{6})(\d{7})(\d{3})")


def parse_frame(frame):
    if len(frame) != 25 or frame[0] != 2 or frame[23] != 3:
        return None
    checksum = 0
    for value in frame[1:24]:
        checksum ^= value
    if checksum != frame[24]:
        return None
    match = PATTERN.fullmatch(frame[1:23])
    if not match:
        return None
    coords = []
    for value, limit in zip(match.groups()[:2], (90, 180)):
        degree, minute, second = int(value[:-4]), int(value[-4:-2]), int(value[-2:])
        if minute >= 60 or second >= 60 or degree > limit:
            return None
        coordinate = degree + minute / 60 + second / 3600
        if coordinate > limit:
            return None
        coords.append(coordinate)
    return (*coords, int(match[3]) * 10)


def decode_nnn_samples(samples, window_start_sample=0, sample_rate=48000):
    """Decode 8N1 transport, retaining seven ASCII bits; validate XOR BCC.

    Sliding tone energy supplies UART transitions. Each start edge resets
    timing, avoiding clock drift across long receive windows.
    Hemisphere is north/east; altitude scaling is inferred from the capture.
    """
    step = sample_rate / 1200
    width = int(round(step))
    if len(samples) < width * 250:
        return []
    x = np.asarray(samples, dtype=np.float64)
    x = x - x.mean()
    t = np.arange(len(x)) / sample_rate
    powers = []
    for frequency in (1200, 2200):
        mixed = x * np.exp(-2j * np.pi * frequency * t)
        summed = np.concatenate(([0j], np.cumsum(mixed)))
        powers.append(np.abs(summed[width:] - summed[:-width]) ** 2)
    bits = (powers[0] > powers[1]).astype(np.uint8)
    found = {}
    for shift in (-0.15, 0, 0.15):
        edges = np.flatnonzero((bits[:-1] == 1) & (bits[1:] == 0)) + 1
        values, offsets = [], []
        next_start = 0
        for edge in edges:
            if edge < next_start:
                continue
            positions = np.rint(edge + (np.arange(10) + 0.5 + shift) * step).astype(int)
            if positions[-1] >= len(bits):
                break
            symbol = bits[positions]
            if symbol[0] or not symbol[9]:
                continue
            values.append(sum(int(symbol[j+1]) << j for j in range(7)))
            offsets.append(int(edge + width / 2))
            next_start = edge + 9.5 * step
        stream = bytes(values)
        for i, value in enumerate(stream):
            if value != 2:
                continue
            frame = stream[i:i+25]
            parsed = parse_frame(frame)
            if parsed is None:
                continue
            offset = window_start_sample + offsets[i]
            key = (round(offset / sample_rate, 1), frame)
            found.setdefault(key, GpsFix(offset, shift, None, None, *parsed, frame.hex()))
    return sorted(found.values(), key=lambda fix: fix.sample_offset)
