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
