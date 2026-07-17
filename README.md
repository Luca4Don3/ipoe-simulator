# IPoE Simulator

跨平台 IPoE DHCP 模拟器，支持 Windows、macOS 和 Linux。

这是一个纯 CLI 的 DHCPv4/IPoE 模拟器。Windows 是第一优先级平台；当前源码同时实现了 macOS 与 Linux 网卡事务后端。CLI 参数、JSON 配置和 DHCP 状态机保持兼容。

本项目旨在为电信 iTV 接口的 IPoE 接入测试提供一个可重复的 DHCPv4 模拟工具，便于在实验和维护环境中验证接入流程、DHCP 参数及网卡状态恢复行为。可替代机顶盒进行接入测试、故障排查、DHCP 参数验证及现场维护。

当前发布状态是“已实现、仅完成离线模拟验证、未完成对应平台实机验证”，不能据此声明生产可用。macOS 与 Linux 尚未进行实机测试，目前仅完成代码层面的平台后端实现与离线验证。本项目不会修改 IPv6，不提供 GUI、开机服务、整机断电时运行的恢复服务或 IPTV 播放能力。

## 支持平台

- Windows 10 x86；
- Windows 10/11 x64；
- Windows 10/11 ARM64；
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

```text
python3 ipoedhcp.py --list-interfaces
python3 ipoedhcp.py --capture-only 30 --interface 12 --capture-output .temp/stb.pcap
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json
python3 ipoedhcp.py --config ipoedhcp_config.json
python3 ipoedhcp.py --config ipoedhcp_config.json --mac <mac> --option60 ITV-STB
```

Windows 可运行 `run.cmd`；macOS/Linux 可运行 `sudo ./run.sh` 进入统筹器。直接拨号必须明确配置 `device.interface`；多网卡时不会默认选择第一张。

## 恢复安全

拨号前会创建 schema v2 恢复日志，包含完整接口标识和平台信息。共享事务按 `snapshot → prepare → apply → restore → verify` 执行。旧版 Windows schema v1 日志仍可恢复，但跨平台日志会被明确拒绝。

恢复状态目录：

- Windows：项目 `.temp/`；
- macOS：`/Library/Application Support/IPoESimulator`；
- Linux：`/var/lib/ipoe-simulator`。

正常 Stop、Ctrl+C、DHCP 失败和可处理异常都会恢复。Windows watchdog 监听父 PID；macOS/Linux watchdog 监听继承管道，父进程异常退出后收到 EOF 并执行恢复。恢复失败时日志保留并记录 `restore_failed`，下一次运行会先重试恢复并拒绝新事务。

纯 CLI 没有开机服务。整机断电后不能在断电期间恢复；下一次启动程序时会根据日志先恢复。

## 验证边界

本开发机只执行以下离线验证，不运行真实网卡修改：

- 源码编译；
- Windows 原有状态验证回归；
- schema v1/v2 恢复兼容；
- 事务 prepare/apply/restore 故障注入；
- macOS/Linux 命令输出解析；
- 跨平台模块导入；
- Windows 三架构及 macOS/Linux 支持平台静态检查；
- CLI 与 JSON 配置兼容检查。

发布前仍必须在 Windows 10/11、macOS 14/15/26，以及各最低版本和现代 Linux 管理器路径上验证正常 Stop、Ctrl+C、DHCP 失败和进程异常后的地址、路由、DNS、DHCP 模式与管理器状态恢复。

## 致谢

感谢以下开源项目为本项目提供支持：

- [Scapy](https://github.com/secdev/scapy)：用于 DHCP 报文构造、二层收发、抓包和 PCAP/PCAPNG 解析；
- [Python](https://www.python.org/)：提供项目运行时及标准库支持。

上述项目仍归其原作者及维护社区所有，并分别遵循各自的许可证和使用条款。
