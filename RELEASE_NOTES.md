# IPoE Simulator v0.5.0

本次 MINOR 更新新增交互式配置管理。“清空配置”必须输入区分大小写的 `CLEAR`，只把 JSON 恢复为完整 `DEFAULT_CONFIG`，不删除 PCAP、日志、runtime 或恢复 journal。

“手动填写”支持 MAC、Option 12/43/50/60/61/125、点分十进制子网掩码、网关、DNS 和抓包时长。Enter 保留当前值，`-` 清空当前字段，无效输入留在当前字段继续输入；全部值先保存在内存草稿中，只有输入 `SAVE` 才原子写入，取消或中断不改变正式配置。JSON schema 与既有 CLI 保持不变。

---

# IPoE Simulator v0.4.2

本修订版强化 Windows 恢复与交互安全：AutomaticMetric 自动模式不再同时写入 InterfaceMetric，手动模式恢复原指标；恢复最多等待 15 秒收敛，失败返回 `5` 并保留 journal。

存在待恢复 journal 时，仅允许查看配置、列出接口、手动恢复和退出，成功恢复并删除 journal 后才解除门禁。交互选卡在 Windows 上优先显示状态为 Up 的非 Bluetooth、非虚拟接口；无安全候选时展示风险原因并要求输入 `USE`。交互抓包始终生成新 PCAP，提取成功前不污染正式配置。

Windows 轻量运行时安装成功后删除本次已校验的下载文件；启动时按 owner/24 小时边界清理遗留 staging 和临时文件，不清理 runtime、PCAP、日志或失败 journal。本说明不表示 `v0.4.2` 已发布，仍需通过 Windows 实机验收。

---

# IPoE Simulator v0.4.1

本版本修复 Windows 运行时启动问题。轻量发布包不再携带 Python；首次启动未找到匹配架构的 Python 3.9–3.14 时，启动器会按锁定清单下载并校验 Python 3.14.6 与 Scapy 2.7.0，再把运行时安装到包内 `runtime` 目录。

PowerShell 运行时按 PowerShell 7、Windows PowerShell 5.1 的顺序逐个验证版本、UTF-8 输出和网络管理命令能力。PowerShell 7 启动失败、版本不兼容或缺少必要能力时会自动回退到能力完整的 Windows PowerShell 5.1；Windows PowerShell 5.1 以 `Function` 形式提供的网络命令也可通过探针。

Windows 轻量附件提供 x86、x64、ARM64 三种架构。本说明不表示 `v0.4.1` 已发布；正式附件仍须经过 Windows CI 和目标设备验证。

---

# IPoE Simulator v0.4.0

本版本将源码支持范围明确为 Python 3.9–3.14，越界版本会显式失败；依赖升级并锁定到 Scapy 2.7.0。Python 3.9 已 EOL，本项目仅承诺应用兼容性，不承诺解释器安全维护。

Windows 便携附件统一使用 Python 3.14.6，仍发布 x86、x64、ARM64 三个 ZIP。三个附件和 `SHA256SUMS.txt` 位于同一个 `v0.4.0` Release；ARM64 必须在原生 ARM64 runner 或实机完成包内运行验证后才可声明验证完成。

macOS 26 不宣称自带 Python。源码运行前必须通过 Command Line Tools 或独立 Python 发行版提供受支持解释器。

---

# IPoE Simulator v0.2.1

本版本补齐仓库与 Windows 发布包中的完整 GNU General Public License v3.0 文本，许可证仍为 GPL-3.0-only。该兼容性修正不改变 DHCP、恢复命令或配置行为。

普通用户请下载与 Windows 原生架构匹配的平台附件：

- `ipoe-simulator-v0.2.1-windows-x86.zip`
- `ipoe-simulator-v0.2.1-windows-x64.zip`
- `ipoe-simulator-v0.2.1-windows-arm64.zip`
- `SHA256SUMS.txt`

此前 `v0.2.0` Release 保持原样，供历史版本使用。

---

# IPoE Simulator v0.2.0

本版本新增独立的手动网卡恢复命令。系统断电或进程异常退出后，可直接从程序默认恢复日志恢复并校验网卡状态，不需要读取拨号配置，也不会检查、安装或加载 Scapy/Npcap。

普通用户请下载与 Windows 原生架构匹配的平台附件：

- `ipoe-simulator-v0.2.0-windows-x86.zip`
- `ipoe-simulator-v0.2.0-windows-x64.zip`
- `ipoe-simulator-v0.2.0-windows-arm64.zip`
- `SHA256SUMS.txt`

恢复命令为 `python3 ipoedhcp.py --restore`；Windows 便携包可使用 `run.cmd --restore`。无待恢复日志时幂等成功；存在日志时必须以管理员/root 权限运行。恢复失败会返回非零退出码并保留日志及失败状态，便于排查和重试。

---

# IPoE Simulator v0.1.1

这是 Windows PowerShell 5.1 兼容性修复版，增加了 Windows PowerShell 5.1 启动脚本语法门禁，并改用兼容性更明确的 UTF-8 编码构造方式。

普通用户请下载与 Windows 原生架构匹配的平台附件：

- `ipoe-simulator-v0.1.1-windows-x86.zip`
- `ipoe-simulator-v0.1.1-windows-x64.zip`
- `ipoe-simulator-v0.1.1-windows-arm64.zip`
- `SHA256SUMS.txt`

此前 `v0.1.0` Release 仍可在历史版本中获取。

---

# IPoE Simulator v0.1.0

这是首个 Windows 正式 Release。

普通用户请下载与 Windows 原生架构匹配的平台附件：

- `ipoe-simulator-v0.1.0-windows-x86.zip`
- `ipoe-simulator-v0.1.0-windows-x64.zip`
- `ipoe-simulator-v0.1.0-windows-arm64.zip`
- `SHA256SUMS.txt`：上述附件的 SHA-256 校验值

每个 ZIP 都包含匹配架构的 Python 3.11.9、Scapy 2.6.1、项目源码和第三方许可证。Npcap 不随包分发；程序仅从 Npcap 官方地址下载，并在执行前校验 Authenticode 签名。

x64 候选包需在普通 Windows 11 上完成双击验证后发布。x86 尚未完成对应硬件实机验证；ARM64 仅完成架构和静态完整性验证。

GitHub 自动生成的 `Source code (zip)` 和 `Source code (tar.gz)` 无法关闭，仅供开发者阅读源码，不包含便携 Python 运行时。普通用户不要下载这两个自动源码归档。
