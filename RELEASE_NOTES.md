# IPoE Simulator v0.6.0

本次向后兼容的 MINOR 更新新增 ChannelList 单播静态路由。`extract_params.py` 可在所选机顶盒 MAC 的 TCP 流中有界重组非标准端口 HTTP，解析 `Content-Length`、chunked、identity 与 gzip 响应，并从明文 `Authentication.CTCSetConfig('Channel', ...)` 提取、过滤、去重最多 256 个 IPv4 单播控制端点。未检测到 ChannelList 时保留旧列表；检测到但解析不完整时原子失败；成功解析时整体替换 `network.unicast_routes`，包括合法空列表。

首次 DHCP ACK 后，Windows 通过临时 `ActiveStore`、Linux 通过 `ip route replace`、macOS 通过 network service additional routes 配置经 ACK 网关转发的 `/32` 路由。存在路由但 ACK 无网关或任一路由应用失败时立即进入事务恢复。schema v2 journal 新增可选 `app_routes`，并在应用前持久化；正常退出、失败恢复、watchdog 和手动恢复均还原原路由快照并检查程序静态路由无残留。INFO 仅记录数量，具体端点限 DEBUG。

PCAP、配置和 DEBUG 日志可能包含运营商控制地址。该功能仅处理明文 ChannelList，不解密 TLS、不登录实时 EPG，也不扫描其他 HTTP 地址。当前版本不得表述为已发布；真实 Windows 10/11 网卡及 x86/x64/ARM64 验收仍是发布前门禁。

项目许可证从 GPL-3.0-only 调整为 GPL-2.0-only，根目录 `LICENSE` 已替换为完整 GNU GPL v2 文本。发布附件同步携带项目许可证、第三方声明和 Scapy 2.7.0 的完整上游 GPL-2.0 许可证文本。Python 运行时采用 PSF License；Npcap 不随项目分发，是受独立许可条款约束的外部运行依赖。本次调整不改变 CLI、`network.unicast_routes`、journal v2 `app_routes` 或三平台网络接口。

---

# IPoE Simulator v0.5.3

本修订版修复 Windows PowerShell 5.1 对无 BOM UTF-8 启动脚本的解析失败，并将 Python 架构探针改为兼容的单引号形式。普通启动仅在非零退出时暂停一次并原样返回退出码；正常退出和所有 `--restore` 路径不暂停。`run.cmd` 记录选用的 PowerShell executable，PowerShell 启动器继续记录完整 edition/version。

Windows 启动器不再在 `--restore` 路径因存在待恢复 journal 而绕过 Python 发现逻辑、强制下载锁定 embeddable runtime；restore 与正常启动共用同一 `Find-Python` 路径，优先复用系统已安装且架构/版本匹配的 Python 3.9–3.14，仅在未命中时才下载锁定运行时，避免在环境已有合适 Python 时仍重复下载。Linux 仅在可工作的 `resolvectl` 或普通 `/etc/resolv.conf` 场景修改 DNS。其他符号链接在网卡修改前显式失败，普通文件通过 `O_NOFOLLOW` 文件描述符写入；旧 journal 的符号链接快照仍可在目标未变化时恢复，失败会保留 journal。Windows 新快照会拒绝“静态 DNS 且服务器为空”的不一致状态，旧 journal 则尽力恢复并通过模式校验显式失败。Windows 恢复阶段静态 DNS 的收敛比较改为集合无关序，并在仅剩 DNS 不一致时先 `-ResetServerAddresses` 再以原快照 `-ServerAddresses` 重发一次，避免 ARM64 上 `Set-DnsClientServerAddress` 与 `Get-DnsClientServerAddress` 偶发不同步使 `--restore` 陷入虚假收敛循环；不匹配时校验会同时报出期望、实际、缺失与多余的服务器，便于排查。

DHCP sniffer 启动等待扩展为固定 5 秒并响应停止事件；Offer/ACK 默认总等待预算调整为 30 秒，并按 4、8、16 秒退避间隔重发请求。参数提取支持整数和嵌套序列形式的 IPv4，非法整数显式报错。发布 workflow 现在按 `VERSION` 精确提取单版本 Release Notes，标题缺失、重复或正文为空时停止发布。公开 CLI、JSON 配置和 journal schema 保持兼容。

本版本已完成本地离线验证；Windows CI 以及真实 Windows PowerShell 5.1/CMD 的启动失败、正常受控停止和 `--restore` 复测仍是发布前门禁。`release-dependencies.json` 的独立签名方案未在本版本实施；同包公钥不能形成独立信任锚，需后续结合 Release 签名与外部信任链设计。

---

# IPoE Simulator v0.5.2

本修订版改进 Windows 安全停止反馈。首次 `Ctrl+C` 会立即显示“已收到停止请求，正在安全恢复，请勿重复按键”，Offer/ACK 与续租等待会响应停止事件；重复中断会按 DHCP Release、网卡恢复与校验、退出阶段提示且不会重入清理。父统筹进程等待子进程安全结束后直接退出，不返回交互菜单；Windows 启动器不再显示结束暂停。正常安全停止返回 `0`，DHCP/Release 失败返回 `4`，恢复或校验失败返回 `5`。

PowerShell 发现逻辑会枚举并去重 `pwsh.exe`，按语义版本只选择最高版本执行 UTF-8 和 NetAdapter/NetTCPIP/DNS cmdlet 能力探针。最高版本不可用或能力不足时直接回退 Windows PowerShell 5.1，不再尝试较旧 `pwsh.exe`；两者都失败时汇总候选路径、版本和原始原因。PowerShell 6.x 保持尽力兼容，但不作为主要实机矩阵。

本版本已完成离线单测、跨平台导入、Windows 三架构静态包校验和敏感信息审计。Windows 10/11 x86/x64/ARM64 完整实机矩阵尚未执行；本次发布经明确授权豁免该门禁，因此不得将本版本表述为已通过完整 Windows 实机验收。

---

# IPoE Simulator v0.5.1

本修订版修复 Windows 恢复被 DHCP 续租阻塞的问题。恢复不再显式执行 `ipconfig /renew`；地址和路由清理、DNS、DHCP 模式、AutomaticMetric 与 InterfaceMetric 的配置写入、状态收敛和校验共享 30 秒总预算。原状态为 DHCP 时允许 Windows 暂时尚未取得新地址，但程序写入的手动地址不得残留。

PowerShell 恢复进程使用受控子进程，超时或异常会终止并等待回收。恢复期间重复 `Ctrl+C` 只记录警告，不会重入恢复。schema v2 journal 追加总恢复次数和最近 20 次的阶段、步骤、时间及错误诊断；仅校验通过后删除 journal。

Windows `run.cmd --restore` 现在直接进入 `ipoedhcp.py --restore`，仅允许附加 `--log-level INFO|DEBUG`，透传退出码且不显示结束暂停。存在 journal 但包内 runtime 缺失时，启动器先安装锁定 runtime，再立即恢复。Windows 11 x64 已完成 DHCP 失败后的自动恢复、待恢复门禁和独立手动恢复实测，本版本确认可用；其他 Windows 版本与架构仍以各自发布门禁结果为准。

交互菜单移除“完整流程”，命令行同步移除 `--all`。抓包、参数提取和直接拨号继续作为独立操作提供，避免自动串联抓包、提取和拨号产生不明确的失败语义。

---

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

本版本当时补齐仓库与 Windows 发布包中的完整 GNU General Public License v3.0 文本，当时许可证为 GPL-3.0-only；项目自 v0.6.0 源码起改为 GPL-2.0-only。该兼容性修正不改变 DHCP、恢复命令或配置行为。

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
