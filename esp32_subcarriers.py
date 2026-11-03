#!/usr/bin/env python3
"""Parse classic ESP32 CSI (ESP-IDF v4.2.4) CSV into ordered complex subcarriers.

Input: 25-column CSI_DATA CSV from classic ESP32 firmware.
Output: One NPZ per (MAC, primary channel, PHY configuration, LTF).

The returned ``subcarrier_index`` is defined relative to the relevant LTF's
own center frequency: LLTF/HT20 use the PRIMARY 20 MHz channel center;
HT40 HT-LTF and STBC-HT-LTF use the bonded 40 MHz center.

When --with-frequency is supplied, ``frequency_hz`` contains absolute RF
frequency for each column of the complex CSI matrix. Frequency calculation
assumes a 312.5 kHz OFDM subcarrier spacing.

Scope: original ESP32 CSI layout in ESP-IDF v4.2.4. Other ESP chips/CSI
formats require a separate layout implementation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

SECONDARY = {0: "none", 1: "above", 2: "below"}
ALL_LTFS = ("lltf", "htltf", "stbc")
SUBCARRIER_SPACING_HZ = 312_500


def get_layout(sig_mode: int, bandwidth: int, secondary: int,
               stbc: int, enabled: set[str]) -> list[tuple[str, np.ndarray]]:
    """Buffer-order subcarrier indices, transcribed from ESP-IDF v4.2.4."""
    if sig_mode not in (0, 1):
        raise ValueError(f"Unsupported sig_mode={sig_mode}; expected 0 or 1")
    if bandwidth not in (0, 1):
        raise ValueError(f"Unsupported bandwidth={bandwidth}; expected 0 or 1")
    if secondary not in SECONDARY:
        raise ValueError(f"Unsupported secondary={secondary}")
    if bandwidth == 1 and secondary == 0:
        raise ValueError("HT40 requires a secondary channel")
    if sig_mode == 0 and (bandwidth != 0 or stbc != 0):
        raise ValueError("Non-HT packet must be 20 MHz without STBC")

    is_stbc = bool(stbc)
    if secondary == 0:
        lltf = np.r_[0:32, -32:0]
    elif secondary == 2:  # Secondary below; primary occupies upper half.
        lltf = np.arange(0, 64)
    else:  # Secondary above; primary occupies lower half.
        lltf = np.arange(-64, 0)

    layout = [("lltf", lltf)] if "lltf" in enabled else []
    if sig_mode == 0:
        return layout

    if bandwidth == 0:  # HT20
        if secondary == 0:
            htltf = np.r_[0:32, -32:0]
        elif secondary == 2:
            htltf = np.arange(0, 63 if is_stbc else 64)
        else:
            htltf = np.arange(-62 if is_stbc else -64, 0)
    else:  # HT40
        if is_stbc:
            htltf = np.r_[0:61, -60:0]
        else:
            htltf = np.r_[0:64, -64:0]

    if "htltf" in enabled:
        layout.append(("htltf", htltf))
    if is_stbc and "stbc" in enabled:
        layout.append(("stbc", htltf.copy()))
    return layout


def normalized_subcarrier_indices(raw_k: np.ndarray, ltf: str,
                                  bandwidth: int, secondary: int
                                  ) -> tuple[np.ndarray, np.ndarray]:
    """Return (buffer mask, ascending normalized physical subcarrier indices).

    LLTF always references the primary 20 MHz center (52 used tones).
    HT20 references primary 20 MHz center (56 used tones).
    HT40 references bonded 40 MHz center (114 used tones).
    """
    if ltf == "lltf" or bandwidth == 0:
        offset = {0: 0, 1: -32, 2: 32}[secondary]
        k = raw_k - offset
        upper = 26 if ltf == "lltf" else 28
    else:
        k = raw_k.copy()
        upper = 57

    mask = (k != 0) & (np.abs(k) <= upper)
    return mask, k[mask]


def primary_center_frequency_hz(channel: int, band: str,
                                override_mhz: float | None = None) -> int:
    """Return primary-channel center in Hz; channel 14 is special in 2.4 GHz."""
    if override_mhz is not None:
        if not np.isfinite(override_mhz) or override_mhz <= 0:
            raise ValueError("--primary-center-mhz must be positive and finite")
        return int(round(override_mhz * 1_000_000))
    if band == "2g":
        if channel == 14:
            return 2_484_000_000
        if not 1 <= channel <= 13:
            raise ValueError(f"Invalid 2.4 GHz channel: {channel}")
        return (2407 + 5 * channel) * 1_000_000
    if band == "5g":
        if not 32 <= channel <= 196:
            raise ValueError(f"Invalid 5 GHz channel: {channel}")
        return (5000 + 5 * channel) * 1_000_000
    raise ValueError(f"Unsupported band: {band}")


def ltf_center_frequency_hz(primary_hz: int, ltf: str, bandwidth: int,
                            secondary: int) -> int:
    """For HT40 HT-LTF/STBC-LTF only, shift to bonded 40 MHz center."""
    if ltf != "lltf" and bandwidth == 1:
        if secondary == 1:  # secondary above primary
            return primary_hz + 10_000_000
        if secondary == 2:  # secondary below primary
            return primary_hz - 10_000_000
        raise ValueError("HT40 requires secondary above or below")
    return primary_hz


def parse_record(row: list[str], enabled: set[str]
                 ) -> tuple[dict, dict[str, dict[str, np.ndarray]]]:
    if len(row) == 15:
        raise RuntimeError(
            "15-column ESP32-C5/C6 CSI format detected. This parser is for "
            "classic ESP32 using the ESP-IDF v4.2.4 25-column format."
        )
    if len(row) != 25:
        raise ValueError(f"Expected 25 fields, got {len(row)}")
    if row[0] != "CSI_DATA":
        raise ValueError("Expected CSI_DATA record")

    meta = {
        "packet_id": int(row[1]),
        "mac": row[2],
        "rssi": int(row[3]),
        "sig_mode": int(row[5]),
        "bandwidth": int(row[7]),
        "stbc": int(row[11]),
        "channel": int(row[16]),
        "secondary": int(row[17]),
        "timestamp": int(row[18]),
        "first_word_invalid": int(row[23]),
    }
    layout = get_layout(meta["sig_mode"], meta["bandwidth"],
                        meta["secondary"], meta["stbc"], enabled)
    if not layout:
        raise ValueError("No LTF enabled/present for this packet")

    raw = np.asarray(json.loads(row[-1]), dtype=np.float32)
    byte_len = int(row[22])
    if len(raw) != byte_len or byte_len % 2:
        raise ValueError(f"CSI byte length mismatch: declared={byte_len}, got={len(raw)}")
    if not np.all(np.isfinite(raw)):
        raise ValueError("Nonfinite CSI raw values")

    content_len = sum(len(k) * 2 for _, k in layout)
    padded_len = content_len + (-content_len % 4)
    if byte_len != padded_len:
        raise ValueError(f"CSI len={byte_len}, expected {padded_len} "
                         f"for selected LTFs {tuple(ltf for ltf, _ in layout)}")

    raw = raw[:content_len]  # Remove tail alignment padding.
    csi = (raw[1::2] + 1j * raw[0::2]).astype(np.complex64)
    if meta["first_word_invalid"]:
        csi[:2] = np.nan + 1j * np.nan

    blocks = {}
    offset = 0
    for ltf, raw_indices in layout:
        length = len(raw_indices)
        values = csi[offset:offset + length]
        offset += length
        mask, normalized_k = normalized_subcarrier_indices(
            raw_indices, ltf, meta["bandwidth"], meta["secondary"]
        )
        ordering = np.argsort(normalized_k)
        blocks[ltf] = {
            "k": normalized_k[ordering].astype(np.int16),
            "csi": values[mask][ordering],
        }
    return meta, blocks


def process_csv(input_file: str, output_dir: str, enabled: set[str],
                with_frequency: bool, band: str,
                primary_center_mhz: float | None) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple, list] = defaultdict(list)
    indices_by_group: dict[tuple, np.ndarray] = {}
    accepted = rejected = 0

    with open(input_file, "r", encoding="utf-8", newline="") as fh:
        for line_number, row in enumerate(csv.reader(fh), start=1):
            if not row or row[0] != "CSI_DATA":
                continue
            try:
                meta, blocks = parse_record(row, enabled)
                if with_frequency:
                    primary_hz = primary_center_frequency_hz(
                        meta["channel"], band, primary_center_mhz
                    )
                else:
                    primary_hz = None
            except RuntimeError:
                raise
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                rejected += 1
                if rejected <= 10:
                    print(f"Skipping CSV row {line_number}: {exc}", file=sys.stderr)
                continue
            accepted += 1

            for ltf, block in blocks.items():
                key = (meta["mac"], meta["channel"], meta["sig_mode"],
                       meta["bandwidth"], meta["secondary"], meta["stbc"], ltf)
                if key in indices_by_group:
                    if not np.array_equal(indices_by_group[key], block["k"]):
                        raise ValueError(f"Inconsistent subcarrier mapping for {key}")
                else:
                    indices_by_group[key] = block["k"]
                groups[key].append((block["csi"], meta, line_number, primary_hz))

    for key, records in groups.items():
        mac, channel, sig, bandwidth, secondary, stbc, ltf = key
        meta_list = [r[1] for r in records]
        k = indices_by_group[key]
        matrix = np.stack([r[0] for r in records])
        filename = (
            f"{mac.replace(':', '').lower()}_ch{channel}_sig{sig}_"
            f"bw{40 if bandwidth else 20}_{SECONDARY[secondary]}_"
            f"stbc{stbc}_{ltf}.npz"
        )
        arrays = {
            "csi": matrix,
            "subcarrier_index": k,
            "valid": np.isfinite(matrix),
            "timestamp": np.asarray([m["timestamp"] for m in meta_list], dtype=np.int64),
            "rssi": np.asarray([m["rssi"] for m in meta_list], dtype=np.int16),
            "packet_id": np.asarray([m["packet_id"] for m in meta_list], dtype=np.int64),
            "row_number": np.asarray([r[2] for r in records], dtype=np.int64),
            "channel": np.int16(channel),
            "bandwidth_mhz": np.int16(40 if bandwidth else 20),
            "secondary_channel": SECONDARY[secondary],
            "ltf": ltf,
        }

        if with_frequency:
            primary_hz = records[0][3]
            center_hz = ltf_center_frequency_hz(
                primary_hz, ltf, bandwidth, secondary
            )
            arrays["primary_center_frequency_hz"] = np.int64(primary_hz)
            arrays["ltf_center_frequency_hz"] = np.int64(center_hz)
            arrays["frequency_hz"] = (
                np.int64(center_hz) + k.astype(np.int64) * SUBCARRIER_SPACING_HZ
            )
        np.savez_compressed(output / filename, **arrays)
        print(f"{filename}: csi={matrix.shape}" +
              (f", center={center_hz / 1e6:g} MHz" if with_frequency else ""))

    print(f"Accepted packets: {accepted}; rejected packets: {rejected}; "
          f"output groups: {len(groups)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Classic ESP32 CSI_DATA CSV input file")
    parser.add_argument("-o", "--output", default="csi_processed")
    parser.add_argument("--ltfs", nargs="+", choices=ALL_LTFS,
                        default=list(ALL_LTFS),
                        help="LTFs enabled in firmware (default: all three)")
    parser.add_argument("--with-frequency", action="store_true",
                        help="Export actual subcarrier RF frequencies in frequency_hz")
    parser.add_argument("--band", choices=("2g", "5g"), default="2g",
                        help="Wi-Fi band used for channel-to-frequency mapping (default: 2g)")
    parser.add_argument("--primary-center-mhz", type=float, default=None,
                        help="Override primary-channel center frequency for all input records; "
                             "use only if every record shares the same center")
    args = parser.parse_args()
    process_csv(args.input, args.output, set(args.ltfs), args.with_frequency,
                args.band, args.primary_center_mhz)


if __name__ == "__main__":
    main()