"""Contract tests against ESP-IDF v4.2.4's 13-row CSI layout table."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from esp32_subcarriers import (
    get_layout,
    ltf_center_frequency_hz,
    normalized_subcarrier_indices,
    parse_record,
    primary_center_frequency_hz,
    process_csv,
)


# (secondary, signal mode, bandwidth, STBC, total CSI bytes)
TABLE = [
    (0, 0, 0, 0, 128),
    (0, 1, 0, 0, 256),
    (0, 1, 0, 1, 384),
    (2, 0, 0, 0, 128),
    (2, 1, 0, 0, 256),
    (2, 1, 0, 1, 380),
    (2, 1, 1, 0, 384),
    (2, 1, 1, 1, 612),
    (1, 0, 0, 0, 128),
    (1, 1, 0, 0, 256),
    (1, 1, 0, 1, 376),
    (1, 1, 1, 0, 384),
    (1, 1, 1, 1, 612),
]


def make_record(secondary, sig_mode, bandwidth, stbc, enabled=frozenset(('lltf', 'htltf', 'stbc')),
                channel=6, invalid=0):
    layout = get_layout(sig_mode, bandwidth, secondary, stbc, set(enabled))
    content_len = 2 * sum(len(k) for _, k in layout)
    padded_len = content_len + (-content_len % 4)
    raw = [0] * padded_len
    # I[k] = k+1 and Q[k] = -(k+1) to verify IQ order.
    for i in range(content_len // 2):
        raw[2 * i] = -(i + 1)
        raw[2 * i + 1] = i + 1
    row = [''] * 25
    row[0] = 'CSI_DATA'
    row[1] = '42'
    row[2] = '94:d9:b3:80:8c:81'
    row[3] = '-40'
    row[5] = str(sig_mode)
    row[7] = str(bandwidth)
    row[11] = str(stbc)
    row[16] = str(channel)
    row[17] = str(secondary)
    row[18] = '1234567'
    row[22] = str(padded_len)
    row[23] = str(invalid)
    row[24] = json.dumps(raw)
    return row


class TestSubcarrierParser(unittest.TestCase):
    def test_official_table_byte_lengths_and_tone_order(self):
        for sec, sig, bw, stbc, expected_bytes in TABLE:
            with self.subTest(sec=sec, sig=sig, bw=bw, stbc=stbc):
                row = make_record(sec, sig, bw, stbc)
                self.assertEqual(int(row[22]), expected_bytes)
                meta, blocks = parse_record(row, {'lltf', 'htltf', 'stbc'})
                self.assertEqual(meta['secondary'], sec)
                for ltf, block in blocks.items():
                    k = block['k']
                    self.assertTrue(np.all(np.diff(k) > 0))
                    upper = 26 if ltf == 'lltf' else (57 if bw else 28)
                    expected = np.r_[np.arange(-upper, 0), np.arange(1, upper + 1)]
                    np.testing.assert_array_equal(k, expected)
                    self.assertEqual(len(block['csi']), len(expected))
                    self.assertTrue(np.all(block['csi'].real > 0))
                    self.assertTrue(np.all(block['csi'].imag < 0))

    def test_enabled_ltfs_and_alignment(self):
        # The official STBC-only HT40 example: 121 IQ pairs + 2 padding bytes.
        row = make_record(1, 1, 1, 1, enabled={'stbc'})
        self.assertEqual(int(row[22]), 244)
        _, blocks = parse_record(row, {'stbc'})
        self.assertEqual(set(blocks), {'stbc'})
        self.assertEqual(len(blocks['stbc']['k']), 114)

    def test_invalid_first_word(self):
        row = make_record(0, 1, 0, 0, invalid=1)
        _, blocks = parse_record(row, {'lltf', 'htltf', 'stbc'})
        # First two invalid IQ pairs are guard/DC for this combination.
        self.assertEqual(len(blocks['lltf']['k']), 52)
        self.assertTrue(np.all(np.isfinite(blocks['htltf']['csi'])))

    def test_frequency_mapping(self):
        primary = primary_center_frequency_hz(6, '2g')
        self.assertEqual(primary, 2437_000_000)
        self.assertEqual(ltf_center_frequency_hz(primary, 'htltf', 1, 1), 2447_000_000)
        self.assertEqual(ltf_center_frequency_hz(primary, 'htltf', 1, 2), 2427_000_000)
        self.assertEqual(ltf_center_frequency_hz(primary, 'lltf', 1, 1), primary)
        self.assertEqual(primary_center_frequency_hz(14, '2g'), 2484_000_000)
        self.assertEqual(primary_center_frequency_hz(36, '5g'), 5180_000_000)

    def test_records_grouped_by_primary_channel(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'in.csv'
            dest = Path(td) / 'out'
            with source.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['type'] + [str(i) for i in range(24)])
                writer.writerow(make_record(0, 1, 0, 0, channel=1))
                writer.writerow(make_record(0, 1, 0, 0, channel=6))
            process_csv(str(source), str(dest), {'lltf', 'htltf'}, True, '2g', None)
            files = list(dest.glob('*.npz'))
            self.assertEqual(len(files), 4)
            for fn in files:
                with np.load(fn) as data:
                    self.assertEqual(data['csi'].shape[0], 1)
                    k = data['subcarrier_index']
                    f = data['frequency_hz']
                    np.testing.assert_array_equal(
                        f,
                        data['ltf_center_frequency_hz'] + k.astype(np.int64) * 312_500,
                    )


if __name__ == '__main__':
    unittest.main()
