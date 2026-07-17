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
