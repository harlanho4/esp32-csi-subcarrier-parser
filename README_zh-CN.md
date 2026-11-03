# ESP32 CSI 子载波解析器

[English](README.md) | 简体中文

面向**原始 ESP32 与 ESP-IDF v4.2.4 CSI 布局**的 CSV 离线解析工具。根据每个接收包的 PHY 配置，自动拆分 LLTF、HT-LTF 和 STBC-HT-LTF，重建复数 CSI，恢复标准化子载波索引并按**实际频率升序**排列。可选输出每个子载波的绝对射频频率（Hz）。

本工具专注于子载波解析，**暂不执行幅度/相位提取、滤波、归一化、相位校正或重采样**。

## 功能

- 输入：经典 ESP32 的 `CSI_DATA` 25 列 CSV。
- 按乐鑫定义的 `[虚部, 实部, 虚部, 实部, ...]` 顺序重建 `complex64` CSI。
- 支持 [ESP-IDF v4.2.4 CSI 文档](https://docs.espressif.com/projects/esp-idf/en/v4.2.4/esp32/api-guides/wifi.html#wi-fi-channel-state-information)第一张表中的 13 种配置组合。
- 根据 `--ltfs` 指定固件启用的训练字段，并处理 4 字节对齐填充。
- 去除 DC 和保护子载波，保留导频子载波，并输出从低频到高频的子载波索引。
- 处理 `first_word_invalid`：用 `NaN` 标记对应无效复数，并保留 `valid` 掩码。
- 按 MAC、主信道、PHY 模式、带宽、辅信道位置、STBC 和 LTF 分组输出。
- 可选恢复实际子载波频率 `frequency_hz`。

> **范围说明：** 针对原始 ESP32 的旧版 25 列 CSI 格式。ESP32-C5/C6 等芯片的 15 列格式需要独立的子载波映射；脚本会拒绝这种格式，避免错误解析。

## 安装与运行

推荐 Python 3.10 或更新版本：

```bash
python -m pip install -r requirements.txt
```

```bash
# 只解析复数 CSI、标准子载波索引
python esp32_subcarriers.py csi_data.csv

# 指定 ESP32 固件中实际启用的 LTF
python esp32_subcarriers.py csi_data.csv --ltfs lltf htltf -o processed

# 额外导出实际射频频率，默认按 2.4 GHz 频段解析
python esp32_subcarriers.py csi_data.csv --with-frequency

# 5 GHz 频段
python esp32_subcarriers.py csi_data.csv --with-frequency --band 5g

# 为全部记录统一指定主信道中心频率（MHz）
python esp32_subcarriers.py csi_data.csv --with-frequency --primary-center-mhz 2412
```

默认输出目录为 `csi_processed/`，每个 `.npz` 文件对应一个固定的 PHY 配置与训练字段。

## 标准化子载波索引

| 训练字段 | 频率参考中心 | 有效索引 | 数量 |
| --- | --- | --- | ---: |
| LLTF | 主 20 MHz 信道中心 | `-26…-1, 1…26` | 52 |
| HT-LTF（HT20） | 主 20 MHz 信道中心 | `-28…-1, 1…28` | 56 |
| HT-LTF（HT40） | 完整 40 MHz 信道中心 | `-57…-1, 1…57` | 114 |
| STBC-HT-LTF | 根据 HT20/HT40 确定 | 对应 HT-LTF | 56 / 114 |

输出位置保留导频子载波，剔除 DC、保护位置以及 STBC 特殊 CSD 位置。不同 `secondary_channel` 配置下的 HT20 最终统一为相同的标准索引。

## 实际频率恢复

使用 `--with-frequency` 后，输出增加 `frequency_hz`、`primary_center_frequency_hz` 和 `ltf_center_frequency_hz`。

计算公式：

$
f_k = f_{\mathrm{LTF\ center}} + k \times 312{,}500\ \mathrm{Hz}
$

LLTF/HT20 使用主 20 MHz 信道中心。HT40 的中心相对于主信道中心在辅信道 `above` 时增加 10 MHz，在 `below` 时减少 10 MHz。

例如主信道 6 的中心为 2437 MHz；HT40 且辅信道为 `above` 时，40 MHz 中心为 2447 MHz，索引 -57 对应 **2429.1875 MHz**。

## 读取输出

```python
import numpy as np

data = np.load("csi_processed/your_htltf.npz")
H = data["csi"]                  # (T, K)，complex64
k = data["subcarrier_index"]      # (K,)，标准索引
valid = data["valid"]              # (T, K)，有效性标记

# 仅在执行时启用 --with-frequency 才包含该字段
freq_mhz = data["frequency_hz"] / 1e6
```

输出同时保留每包的 `timestamp`、`rssi`、`packet_id`、原 CSV 行号 `row_number`，以及信道、带宽、辅信道和 LTF 元数据。

**注意：** `--ltfs` 应与 ESP32 固件的 `wifi_csi_config_t` 对应；原始 25 列 CSV 不包含这些启用标志。`--primary-center-mhz` 将作用于所有输入记录，适用于中心频率固定的采集文件。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖 13 种官方布局、子载波索引与排序、LTF 选择、填充对齐、主信道分组和频率恢复；测试使用合成 CSV，并非实际射频测量验证。

## 参考与许可证

- [ESP-IDF v4.2.4 CSI 文档](https://docs.espressif.com/projects/esp-idf/en/v4.2.4/esp32/api-guides/wifi.html#wi-fi-channel-state-information)
- [Espressif esp-csi 官方示例](https://github.com/espressif/esp-csi/tree/master/examples/get-started/tools)
- [MIT License](LICENSE)