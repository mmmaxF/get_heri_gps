import unittest
import numpy as np
from nnn_demodulator import decode_nnn_samples, parse_frame


FRAME = bytes.fromhex('02313030203139333532343538313335313630353131370315')


def audio(frame):
    bits = [1] * 80
    for value in frame:
        # Seven ASCII bits plus even parity in the eighth UART data position.
        value |= (value.bit_count() % 2) << 7
        bits += [0] + [(value >> j) & 1 for j in range(8)] + [1]
    bits += [1] * 80
    frequencies = np.repeat(np.where(bits, 1200, 2200), 40)
    phase = np.cumsum(2 * np.pi * frequencies / 48000)
    return (8000 * np.sin(phase)).astype(np.int16)


class NnnTests(unittest.TestCase):
    def test_coordinates_and_checksum(self):
        lat, lon, alt = parse_frame(FRAME)
        self.assertAlmostEqual(lat, 35 + 24/60 + 58/3600)
        self.assertAlmostEqual(lon, 135 + 16/60 + 5/3600)
        self.assertEqual(alt, 1170)
        self.assertIsNone(parse_frame(FRAME[:-1] + b'\x00'))

    def test_audio_timing_gain_and_dc(self):
        samples = audio(FRAME)
        for trim in (0, 7, 23):
            fixes = decode_nnn_samples(samples[trim:] // 2 + 500, trim)
            self.assertEqual(len(fixes), 1)
            self.assertEqual(fixes[0].payload_hex, FRAME.hex())
            self.assertIsNone(fixes[0].aircraft)

    def test_invalid_and_silent_audio(self):
        self.assertEqual(decode_nnn_samples(audio(FRAME[:-1] + b'\x00')), [])
        self.assertEqual(decode_nnn_samples(np.zeros(48000)), [])
        self.assertEqual(decode_nnn_samples(np.zeros(20)), [])


if __name__ == '__main__':
    unittest.main()
