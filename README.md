# IPoE Simulator

正式支持 Windows 的 IPoE DHCP 模拟器；macOS 和 Linux 仅完成代码适配，不发布平台附件。

这是一个纯 CLI 的 DHCPv4/IPoE 模拟器。Windows 是第一优先级平台；当前源码同时实现了 macOS 与 Linux 网卡事务后端。CLI 参数、JSON 配置和 DHCP 状态机保持兼容。项目源码采用 GPL-3.0-only，详见 `LICENSE`。

本项目旨在为电信 iTV 接口的 IPoE 接入测试提供一个可重复的 DHCPv4 模拟工具，便于在实验和维护环境中验证接入流程、DHCP 参数及网卡状态恢复行为。可替代机顶盒进行接入测试、故障排查、DHCP 参数验证及现场维护。

当前发布状态不能据此声明生产可用。x64 Release 候选包需要在普通 Windows 11 上完成双击实测；x86 尚未完成对应硬件实机验证，ARM64 仅完成架构和静态完整性验证。macOS 与 Linux 尚未进行实机测试，目前仅完成代码层面的平台后端实现与离线验证。本项目不会修改 IPv6，不提供 GUI、开机服务、整机断电时运行的恢复服务或 IPTV 播放能力。

## 下载

普通用户请从 GitHub Release 下载与 Windows 原生架构匹配的附件：

- `ipoe-simulator-v0.1.0-windows-x86.zip`
- `ipoe-simulator-v0.1.0-windows-x64.zip`
- `ipoe-simulator-v0.1.0-windows-arm64.zip`

使用同一 Release 中的 `SHA256SUMS.txt` 校验下载文件。每个 ZIP 只有一个顶层目录，自带匹配架构的 Python 3.11.9 和固定版本 Scapy，只提供 `run.cmd`，不包含 `run.sh`。Npcap 不随包分发，仍从官方地址下载并在安装前校验 Authenticode 签名。

GitHub 自动生成的 `Source code (zip)` 和 `Source code (tar.gz)` 无法关闭，仅供开发者使用，不包含便携运行时；普通用户应下载上述平台附件。

## 代码适配范围

- Windows 10 x86（正式支持，尚未实机验证）；
- Windows 10/11 x64（正式支持）；
- Windows 10/11 ARM64（正式支持，仅完成静态验证）；
- macOS 14、15、26，Intel x86_64 与 Apple Silicon；
- RHEL/CentOS 6.5+；
- Ubuntu 12.04 LTS+；
- SLES 11 SP3+；
- openSUSE 13.1+；
- 其他满足能力检测的现代 Linux。

遗留 Linux 只支持 x86_64，必须使用 `/opt/ipoe-simulator/runtime` 内随部署包提供的 Python 3.11 + Scapy。现代 Linux 支持 x86_64 和 aarch64。源码仓库不包含跨发行版 Python 二进制包；缺少指定 runtime 时程序会显式失败，不会退回系统旧版 Python。

## 平台后端

Windows 使用 PowerShell NetTCPIP/DNS cmdlet 和 Npcap，保存并恢复 IPv4 地址、路由、DNS、DHCP、AutomaticMetric 和 InterfaceMetric。

macOS 使用系统 `networksetup`、`ifconfig`、`route` 与 `libpcap/BPF`，保存并恢复 network service 的启用状态、DHCP/手动模式、IPv4、网关、DNS 和附加路由。

Linux 使用 Scapy PF_PACKET 和 `iproute2`，同时兼容现代 `ip -j` 与旧版 `ip -o` 输出。支持：

- NetworkManager；
- systemd-networkd；
- ifupdown；
- RHEL/CentOS sysconfig；
- SLES/openSUSE sysconfig；
- 未托管静态接口。

如果接口带动态地址但无法归属到已支持的管理器，或者发现未知动态管理进程，程序会在修改前失败。依赖检测可识别 `yum`、`dnf`、`apt-get`、`zypper` 和 `pacman`，只安装 `iproute2/iproute` 与 `tcpdump` 等基础依赖，不会安装或替换网络管理器。仓库失效时保留包管理器原始错误并退出。

## 权限与依赖

需要 Python 3.10+ 和 Scapy 2.5+。遗留 Linux 固定使用部署 runtime 中的 Python 3.11。

- Windows 要求管理员权限，优先使用 PowerShell 7（`pwsh.exe`）；未安装时回退到 Windows PowerShell 5.1（`powershell.exe`）。两者都必须以管理员身份运行。运行时可自动安装 Scapy，并在 Npcap 缺失时从官方地址下载、使用已选择的 PowerShell 校验 Authenticode 签名后静默安装。
- macOS/Linux 要求 `sudo/root`，程序不会自动提权。
- macOS 使用系统网络工具与系统 `libpcap/BPF`。
- Linux 使用 PF_PACKET；过滤器编译依赖 `tcpdump`。

每个平台和架构必须使用匹配的 Python 解释器。程序会拒绝在 x64/ARM64 Windows 上用 32 位 Python 执行拨号。

先执行只读环境检测：

```text
python3 check_env.py
python3 check_env.py --json
```

POSIX 环境检测不会安装依赖，也不会修改网卡。Windows 维持原有 Scapy 自动安装行为。

## 使用

项目提供环境检测、抓包、参数提取、直接拨号和统筹器五类命令。所有命令均在项目根目录执行；Windows 运行网络事务时使用管理员权限。

### 环境检测

环境检测执行只读检查，覆盖 Python、Scapy、权限、平台、架构和网络依赖：

```text
python3 check_env.py
python3 check_env.py --json
```

`--json` 以 JSON 输出检测结果，适合脚本和 CI 读取。

### 直接使用 `ipoedhcp.py`

列出可用网卡：

```text
python3 ipoedhcp.py --list-interfaces
```

`--interface` 支持网卡 GUID、ifIndex、名称或唯一描述。多网卡环境请明确指定网卡。

只抓取 DHCP 报文并保存 PCAP：

```text
python3 ipoedhcp.py --capture-only 30 --interface 12 --capture-output .temp/stb.pcap
```

`--capture-only` 的数值单位为秒；省略数值时默认抓包 30 秒。`--capture-output` 指定 PCAP/PCAPNG 输出路径，省略时写入项目 `.temp/` 目录。

使用 JSON 配置启动 IPoE DHCP 模拟：

```text
python3 ipoedhcp.py --config ipoedhcp_config.json
python3 ipoedhcp.py --config ipoedhcp_config.json --mac <mac> --option60 ITV-STB
python3 ipoedhcp.py --config ipoedhcp_config.json --interface 12 --timeout 8 --log-level INFO
```

常用参数：

- `--config`、`-c`：JSON 配置路径；
- `--mac`、`-m`：覆盖配置中的机顶盒 MAC；
- `--interface`：覆盖配置中的网卡；
- `--option12`、`--option43`、`--option50`、`--option60`、`--option61`、`--option125`：覆盖 DHCP 选项；
- `--timeout`：Offer/ACK 等待秒数，默认 8 秒；
- `--log-level`：选择 `DEBUG` 或 `INFO` 日志级别。

程序启动 DHCP 事务前保存网卡快照，结束时执行恢复和校验。正常停止使用 `Ctrl+C`；DHCP 失败、可处理异常和父进程异常退出均进入恢复流程。

### 使用 `extract_params.py` 提取参数

从 PCAP 或 PCAPNG 文件提取 DHCP 参数并打印 JSON：

```text
python3 extract_params.py .temp/stb.pcap
```

将提取结果合并写入配置文件：

```text
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json --stb-mac <mac>
```

`--stb-mac` 用于多客户端抓包时选择目标机顶盒。提取完成后可以直接使用生成或更新的 JSON 配置启动拨号。

### 使用 `coordinator.py` 统筹流程

统筹器可以分步执行抓包、参数提取和 DHCP 模拟：

```text
python3 coordinator.py --capture 30 --interface 12
python3 coordinator.py --extract .temp/stb.pcap
python3 coordinator.py --dhcp
```

执行完整流程：

```text
python3 coordinator.py --all --interface 12 --duration 30
```

常用管理参数：

```text
python3 coordinator.py --show
python3 coordinator.py --reset
python3 coordinator.py --interactive
python3 coordinator.py --config ipoedhcp_config.json --log-level INFO
```

`--show` 显示当前 JSON 配置，`--reset` 恢复默认配置，`--interactive` 进入交互菜单。`--capture`、`--extract`、`--dhcp` 可以组合使用；`--pcap` 指定抓包文件，`--duration` 指定抓包秒数。

### Windows 启动器

Windows 可运行：

```text
run.cmd --show
run.cmd --config ipoedhcp_config.json --interactive
```

启动器优先选择 PowerShell 7，并兼容回退到 Windows PowerShell 5.1。macOS/Linux 可运行 `sudo ./run.sh` 进入统筹器。

### 恢复命令与自动恢复

恢复入口采用 DHCP 事务自动流程，用户通过启动或再次启动程序进入恢复流程。当前 CLI 的恢复能力以内置事务和 watchdog 方式提供。

1. 拨号开始前创建 schema v2 恢复日志；
2. 正常 Stop、`Ctrl+C`、DHCP 失败和可处理异常时恢复网卡；
3. 父进程异常退出时，由 watchdog 读取恢复日志并执行恢复；
4. 下一次启动时，程序先检查待恢复日志并完成恢复，再开始新的 DHCP 事务；
5. 恢复成功后删除日志，恢复失败时保留日志并记录错误，后续启动继续重试。

恢复状态目录：

- Windows：项目 `.temp/`；
- macOS：`/Library/Application Support/IPoESimulator`；
- Linux：`/var/lib/ipoe-simulator`。

## 恢复安全

拨号前创建 schema v2 恢复日志，记录完整接口标识和平台信息。共享事务按 `snapshot → prepare → apply → restore → verify` 执行。恢复流程兼容旧版 Windows schema v1，并对跨平台日志执行平台校验。

Windows watchdog 监听父 PID；macOS/Linux watchdog 监听继承管道。恢复失败时保留日志并记录 `restore_failed`，下一次运行优先处理待恢复日志。

纯 CLI 运行模式提供进程生命周期内的恢复能力；整机重新启动后，程序根据保留的恢复日志执行恢复。

## 致谢

感谢以下开源项目为本项目提供支持：

- [Scapy](https://github.com/secdev/scapy)：用于 DHCP 报文构造、二层收发、抓包和 PCAP/PCAPNG 解析；
- [Python](https://www.python.org/)：提供项目运行时及标准库支持。

上述项目仍归其原作者及维护社区所有，并分别遵循各自的许可证和使用条款。
