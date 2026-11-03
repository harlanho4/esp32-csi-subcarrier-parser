# ESP32 CSI Subcarrier Parser

[简体中文](README_zh-CN.md) | English

Parse CSI CSV captures from the **original ESP32** (ESP-IDF **v4.2.4** CSI layout) into complex NumPy arrays with **standardized, ascending physical subcarrier indices**. Optionally reconstruct the absolute RF frequency of each subcarrier.

The parser focuses exclusively on subcarrier structure. It does **not** calculate amplitude or phase, perform filtering, phase sanitization, calibration, or resampling.

## Features

- Reads the classic ESP32 `CSI_DATA` **25-column** CSV format.
- Decodes signed I/Q values in Espressif's `[imag, real, imag, real, …]` order into `complex64` samples.
- Supports all **13 CSI layouts** in the [ESP-IDF v4.2.4 CSI table](https://docs.espressif.com/projects/esp-idf/en/v4.2.4/esp32/api-guides/wifi.html#wi-fi-channel-state-information): non-HT / HT, 20 / 40 MHz, STBC, and secondary-channel placement.
- Separates LLTF, HT-LTF and STBC-HT-LTF, with optional LTFs controlled by `--ltfs`.
- Removes DC and guard subcarriers, retains **pilot subcarriers**, and sorts the physical frequency positions from low to high.
- Handles `first_word_invalid`, optional LTFs, and 4-byte trailing CSI buffer alignment.
- Groups outputs by MAC, **primary channel**, PHY configuration, and LTF.
- Optionally emits **actual RF frequencies in Hz** with `--with-frequency`.

> **Compatibility:** This is for the **original ESP32** with the legacy CSI layout and 25-column CSV, as described in the linked ESP-IDF version. The 15-column ESP32-C5/C6 captures and other chips' CSI formats need a different mapping. The parser explicitly rejects 15-column captures.

## Installation

Python 3.10+ is recommended.

```bash
python -m pip install -r requirements.txt
```

## Usage

```bash
# Default: decode CSI with normalized subcarrier indices only
python esp32_subcarriers.py csi_data.csv

# Specify which LTFs were enabled in your ESP32 firmware
python esp32_subcarriers.py csi_data.csv --ltfs lltf htltf -o processed

# Export absolute frequencies for 2.4 GHz captures (default band)
python esp32_subcarriers.py csi_data.csv --with-frequency

# Export absolute frequencies for 5 GHz captures
python esp32_subcarriers.py csi_data.csv --with-frequency --band 5g

# Override the primary channel center for *all* records
python esp32_subcarriers.py csi_data.csv --with-frequency --primary-center-mhz 2412
```

The default output directory is `csi_processed/`. Each `.npz` file corresponds to a fixed `(MAC, primary channel, PHY mode, bandwidth, secondary-channel placement, STBC, LTF)` combination.

### Example output

```text
csi_processed/
├── 94d9b3808c81_ch6_sig1_bw20_none_stbc0_lltf.npz
└── 94d9b3808c81_ch6_sig1_bw20_none_stbc0_htltf.npz
```

```python
import numpy as np

data = np.load("csi_processed/94d9b3808c81_ch6_sig1_bw20_none_stbc0_htltf.npz")
H = data["csi"]                       # complex64, shape (frames, 56)
k = data["subcarrier_index"]           # [-28, ...,-1, 1,...,28]
valid = data["valid"]                   # bool mask for invalid samples
print(H.shape, k.shape)

# Available only with --with-frequency
frequency_mhz = data["frequency_hz"] / 1e6
```

### Output fields

| Key | Meaning |
| --- | --- |
| `csi` | `(T, K)` complex64 matrix, CSI by packet and ascending subcarrier frequency |
| `subcarrier_index` | `(K,)` standardized integer subcarrier indices |
| `valid` | `(T, K)` Boolean validity mask (`False` for invalid CSI samples) |
| `timestamp` | `(T,)` receiver-local timestamps from captured CSV |
| `rssi` | `(T,)` RSSI per received packet |
| `packet_id` | `(T,)` captured packet identifier |
| `row_number` | `(T,)` original CSV line numbers |
| `channel` | Primary Wi-Fi channel number |
| `bandwidth_mhz` | 20 or 40 |
| `secondary_channel` | `none`, `above`, or `below` |
| `ltf` | `lltf`, `htltf`, or `stbc` |
| `frequency_hz` | `(K,)` absolute subcarrier RF frequencies, present with `--with-frequency` |
| `primary_center_frequency_hz` | Primary 20 MHz center, present with `--with-frequency` |
| `ltf_center_frequency_hz` | Center used for this LTF's index, present with `--with-frequency` |

## Subcarrier indexing

The ESP32 raw CSI buffer sometimes uses circular FFT order or indices referenced to a 40 MHz channel center. The script maps samples to **standardized indices relative to the center of the actual LTF bandwidth**, then sorts them numerically (ascending RF frequency).

| Training field | Reference center | Selected indices | Complex samples |
| --- | --- | --- | ---: |
| LLTF | Primary 20 MHz channel | `-26...-1, 1...26` | 52 |
| HT-LTF, HT20 | Primary 20 MHz channel | `-28...-1, 1...28` | 56 |
| HT-LTF, HT40 | Bonded 40 MHz channel | `-57...-1, 1...57` | 114 |
| STBC-HT-LTF | 20 MHz or 40 MHz center, according to packet | Same as HT-LTF at that bandwidth | 56 / 114 |

Pilot locations are **included**. The STBC-specific CSD value at the DC position is not retained as channel CSI.

For an 802.11n HT20 packet with the secondary channel **below** the primary, the raw buffer's 40 MHz-referenced tone index may be `k40 = k20 + 32`. Standardization subtracts this offset; the resulting HT20 indices are always `-28...-1, 1...28`. When the secondary is **above**, the raw index is shifted by `-32`, which the parser compensates for.

## Absolute RF frequencies

Activate `--with-frequency` to add a `frequency_hz` vector to each `.npz` file:

\[
f_k = f_{\mathrm{LTF\ center}} + k \times 312{,}500\ \mathrm{Hz}.
\]

- **LLTF / HT20:** center is the primary 20 MHz channel center.
- **HT40:** center is the primary channel center **+10 MHz** when the secondary channel is `above`, or **−10 MHz** when `below`.
- **2.4 GHz:** standard channel numbering (`1..13`: `2407 + 5 × channel` MHz; channel 14: 2484 MHz).
- **5 GHz:** `5000 + 5 × channel` MHz (within the supported channel range).
- `--primary-center-mhz` overrides the primary center on *every input record*, so use it only for captures on a single known center frequency.

**Example:** Primary channel 6 has a 2.4 GHz center at 2437 MHz. For HT40 with secondary `above`, the bonded-channel center is 2447 MHz. Subcarrier `k = -57` therefore corresponds to 2429.1875 MHz.

The primary channel number, PHY metadata, and LTF selection must match the firmware configuration. Input CSI may already have been altered by receiver hardware and firmware gain processing; this parser preserves the complex values it receives, apart from marking an invalid first word.

## Testing

```bash
python -m unittest discover -s tests -v
```

The tests cover the official 13 layout combinations, expected byte lengths, ascending/normalized indices, 4-byte alignment, and frequency conversions.

## References

- [Espressif ESP-IDF v4.2.4 — Wi-Fi Channel State Information](https://docs.espressif.com/projects/esp-idf/en/v4.2.4/esp32/api-guides/wifi.html#wi-fi-channel-state-information)
- [Espressif esp-csi — get-started tools](https://github.com/espressif/esp-csi/tree/master/examples/get-started/tools)

## License

[MIT](LICENSE)