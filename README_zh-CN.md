# ESP32 CSI 子载波解析器

[English](README.md) | 简体中文

面向**ESP-IDF v5.5（兼容 v4.2.4）传统 25 列 CSI 格式**的离线解析工具。根据每个接收包的 PHY 配置，自动拆分 LLTF、HT-LTF 和 STBC-HT-LTF，重建复数 CSI，恢复标准化子载波索引并按**实际频率升序**排列。可选输出每个子载波的绝对射频频率（Hz）。

本工具专注于子载波解析，**暂不执行幅度/相位提取、滤波、归一化、相位校正或重采样**。

## 功能

- 输入：经典 ESP32 的 `CSI_DATA` 25 列 CSV。
- 按乐鑫定义的 `[虚部, 实部, 虚部, 实部, ...]` 顺序重建 `complex64` CSI。
- 支持 [ESP-IDF v5.5 CSI 文档](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-guides/wifi.html#wi-fi-channel-state-information)中与 v4.2.4 一致的 13 种传统 CSI 配置组合。
- 根据 `--ltfs` 指定固件启用的训练字段，并处理 4 字节对齐填充。
- 去除 DC 和保护子载波，保留导频子载波，并输出从低频到高频的子载波索引。
- 处理 `first_word_invalid`：用 `NaN` 标记对应无效复数，并保留 `valid` 掩码。
- 校验 25 列 CSV 表头，兼容 v5.5 示例中的 `rx_format` 与旧版 `rx_state` 字段名。
- 按 MAC、主信道、接收天线 `ant`、PHY 模式、带宽、辅信道位置、STBC 和 LTF 分组输出。
- 可选恢复实际子载波频率 `frequency_hz`。

> **v5.5 兼容范围：** 原始 ESP32 的 CSI 数据布局在 v4.2.4 与 v5.5 之间保持一致。对于 ESP32-S2、ESP32-S3、ESP32-C3，如果固件输出相同的 **25 列传统 CSI CSV**，也可使用对应布局。可通过 `--chip` 声明目标芯片，输出文件会保留该元数据。
>
> **格式限制：** ESP32-C5/C6 的官方示例使用 **15 列 CSV**，且 C5 存在不同的 PHY/LTF 子载波映射。仅依据 15 列中的 `len` 和 `rx_format` 无法可靠恢复所有必需的信道布局，因此当前脚本会主动拒绝此类记录，避免产生错误结果。

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

# 指定 ESP-IDF v5.5 的传统 CSI 布局芯片
python esp32_subcarriers.py csi_data.csv --chip esp32s3 --idf-version v5.5

# 使用 ESP-IDF v4.2.4 采集的数据
python esp32_subcarriers.py csi_data.csv --chip esp32 --idf-version v4.2.4

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

$$
f_k = f_{\mathrm{LTF\ center}} + k \times 312{,}500\ \mathrm{Hz}
$$

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

输出同时保留每包的 `timestamp`、`rssi`、`packet_id`、原 CSV 行号 `row_number`，以及信道、接收天线 `antenna`、带宽、辅信道、LTF、`chip` 和 `idf_version` 元数据。不同接收天线会保存到不同的 NPZ 文件。

**注意：** `--ltfs` 应与 ESP32 固件的 `wifi_csi_config_t` 对应；原始 25 列 CSV 不包含这些启用标志。`--chip` 仅用于说明输入采集芯片，不执行不同芯片 CSI 布局之间的转换。当前覆盖的传统 25 列芯片主要使用 2.4 GHz Wi-Fi；5 GHz 计算参数保留用于明确知道中心频率时的手动使用。`--primary-center-mhz` 将作用于所有输入记录，适用于中心频率固定的采集文件。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖 ESP-IDF v5.5 中的 13 种传统布局、子载波排序、LTF 选择、对齐、v5.5 表头检查、15 列拒绝、接收天线分组和频率恢复。测试使用合成 CSV，尚未进行 v5.5 设备实测。

## 参考与许可证

- [ESP-IDF v5.5 CSI 文档](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-guides/wifi.html#wi-fi-channel-state-information)
- [ESP-IDF v5.5 ESP32-C5 CSI 格式](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32c5/api-guides/wifi.html#wi-fi-channel-state-information)
- [ESP-IDF v4.2.4 CSI 文档](https://docs.espressif.com/projects/esp-idf/en/v4.2.4/esp32/api-guides/wifi.html#wi-fi-channel-state-information)
- [Espressif esp-csi 官方示例](https://github.com/espressif/esp-csi/tree/master/examples/get-started/tools)
- [MIT License](LICENSE)