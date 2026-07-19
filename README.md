# IPoE Simulator

正式支持 Windows 10/11 x86、x64 和 ARM64 的 IPoE DHCP 模拟器；macOS 和 Linux 仅完成代码适配，不发布平台附件。

这是一个纯 CLI 的 DHCPv4/IPoE 模拟器。Windows 是第一优先级平台；当前源码同时实现了 macOS 与 Linux 网卡事务后端。CLI 参数、JSON 配置和 DHCP 状态机保持兼容。项目源码采用 GPL-3.0-only，详见 `LICENSE`。

本项目旨在为电信 iTV 接口的 IPoE 接入测试提供一个可重复的 DHCPv4 模拟工具，便于在实验和维护环境中验证接入流程、DHCP 参数及网卡状态恢复行为。可替代机顶盒进行接入测试、故障排查、DHCP 参数验证及现场维护。

`v0.2.1` 的 Windows x86、x64、ARM64 附件已经发布；源码包含对应架构适配，Windows 10/11 三种架构均正式支持。macOS 与 Linux 尚未进行实机测试，目前仅完成代码层面的平台后端实现与离线验证。本项目不会修改 IPv6，不提供 GUI、开机服务、整机断电时运行的恢复服务或 IPTV 播放能力。

## 下载

普通用户请从 GitHub Release 下载与 Windows 原生架构匹配的附件：

- `ipoe-simulator-v0.2.1-windows-x86.zip`
- `ipoe-simulator-v0.2.1-windows-x64.zip`
- `ipoe-simulator-v0.2.1-windows-arm64.zip`

三个架构附件位于同一个版本 Release，使用同一份 `SHA256SUMS.txt` 校验。每个 ZIP 只有一个顶层目录，自带匹配架构的 Python 3.14.6 和 Scapy 2.7.0；Npcap 不随包分发，安装前同时校验 SHA-256、Authenticode 状态及发布者。GitHub 自动生成的源码归档仅供开发者使用，不包含便携运行时。

## 配置示例

`config.example.json` 是现有配置结构的参考。复制后请替换所有占位符；不要把占位符直接用于真实拨号。

最小配置（只指定接口和机顶盒 MAC）：

```json
{
  "device": {
    "mac": "<mac>",
    "interface": "<interface>"
  }
}
```

通用 iTV 完整配置示例（Option 值必须来自你的授权抓包或运营商提供的测试资料）：

```json
{
  "device": {
    "mac": "<mac>",
    "interface": "<interface>"
  },
  "dhcp_options": {
    "option12": "<hostname>",
    "option43": "<option43_hex>",
    "option50": "<requested_ipv4>",
    "option60": "<vendor_class>",
    "option61": "<option61_hex>",
    "option125": "<option125_hex>"
  },
  "capture": {
    "pcap_file": ".temp/<capture>.pcap",
    "duration": 30
  },
  "network": {
    "subnet_mask": "",
    "gateway": "",
    "dns": []
  },
  "behavior": {
    "auto_renew": true,
    "restore_on_exit": true
  },
  "logging": {
    "directory": ".temp/logs"
  }
}
```

DHCP 选项格式：Option 12 是 UTF-8 主机名；Option 43、61、125 使用 `0x` 开头的连续十六进制字节串；Option 50 是 IPv4 地址（或 `0x` 加 4 个字节）；Option 60 是 UTF-8 厂商类字符串。未使用的选项留空。选项内容通常来自授权网络中机顶盒的 DHCP Discover/Request 抓包，优先使用 `extract_params.py` 提取并人工复核；不要猜测、拼接或照搬其他用户的标识。Option 43/125 的内部 TLV 和厂商编码没有通用标准，必须按实际网络资料解释。

## 安全使用与失败处理

仅在你拥有授权的实验网或维护网中运行，并使用独立的测试网卡；不要在承载日常办公、生产业务或未知 DHCP 服务的接口上拨号。程序会保存并恢复 IPv4 地址、路由、DNS、DHCP 及网卡指标，运行期间这些设置可能短暂改变；IPv6 不在恢复范围内。

正常停止请使用 `Ctrl+C` 或程序的 Stop 流程。DHCP 失败、可处理异常和父进程异常会触发恢复；恢复日志位于 Windows 项目 `.temp/network-recovery.json`、macOS `/Library/Application Support/IPoESimulator/network-recovery.json` 或 Linux `/var/lib/ipoe-simulator/network-recovery.json`。若日志显示 `restore_failed`，保留日志和接口现场，勿反复强行拨号；先检查权限、接口是否仍存在及网络管理器状态，再按日志重试恢复。整机断电不保证执行进程内恢复，重启后应先运行手动恢复命令，并人工核对 IPv4、路由、DNS 和 DHCP 状态。

PCAP、日志和恢复日志可能包含 MAC、IP、接口名称、厂商标识及运营商专有字段。共享前请脱敏并限制文件权限；不要提交到公开仓库。抓包或拨号失败时应保留脱敏后的错误上下文和退出码，避免公开原始报文、完整配置或凭据。

## 代码适配范围

- Windows 10 x86（正式支持）；
- Windows 10/11 x64（正式支持）；
- Windows 10/11 ARM64（正式支持）；
- macOS 14、15、26，Intel x86_64 与 Apple Silicon；
- Linux 为未交付 runtime、未实机验证的适配目标。

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

源码正式支持 Python 3.9、3.10、3.11、3.12、3.13 和 3.14，并锁定 Scapy 2.7.0。Python 3.8 及更低版本、Python 3.15 及更高版本会显式失败。`release-dependencies.json` 记录 Windows 便携 Python、Scapy 与 Npcap 的版本、URL 和 SHA-256；打包与运行时安装均只接受该清单，已安装的 Scapy 版本不匹配也会失败。

Python 3.9 已结束上游安全维护；项目只承诺应用代码兼容，不承诺 Python 3.9 解释器的安全维护。源码支持矩阵与 Windows 包内固定运行时相互独立：Windows Release 仍只提供 Python 3.14.6 的 x86、x64、ARM64 三个附件，不为每个源码支持版本分别打包。

- Windows 要求管理员权限，优先使用 PowerShell 7（`pwsh.exe`）；未安装时回退到 Windows PowerShell 5.1（`powershell.exe`）。两者都必须以管理员身份运行。运行时可自动安装 Scapy，并在 Npcap 缺失时从官方地址下载、使用已选择的 PowerShell 校验 Authenticode 签名后静默安装。
- macOS/Linux 要求 `sudo/root`，程序不会自动提权。
- macOS 26 不应被视为自带 Python。运行源码前须通过 Command Line Tools 或 Python 官方/可信发行版安装 Python 3.9–3.14；若 `python3` 只是不可用的系统 shim 或解释器缺失，启动器会提示安装，不会静默继续。
- macOS 使用系统网络工具与系统 `libpcap/BPF`。
- Linux 使用 PF_PACKET；过滤器编译依赖 `tcpdump`。

每个平台和架构必须使用匹配的 Python 解释器。程序会拒绝在 x64/ARM64 Windows 上用 32 位 Python 执行拨号。

先执行只读环境检测：

```text
python3 check_env.py
python3 check_env.py --json
```

所有平台的环境检测均不会安装依赖，也不会修改网卡；抓包或拨号才会安装已锁定依赖。

## 使用

项目提供环境检测、抓包、参数提取、直接拨号、手动恢复和统筹器六类命令。所有命令均在项目根目录执行；Windows 运行网络事务时使用管理员权限。

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

手动恢复程序默认 journal：

```text
python3 ipoedhcp.py --restore
python3 ipoedhcp.py --restore --log-level DEBUG
```

`--restore` 是独立主操作，不能与抓包、列出接口或拨号主操作组合。它在读取 `ipoedhcp_config.json` 前执行，不读取拨号配置，也不检查、安装或加载 Scapy/Npcap。没有默认 journal 时会明确记录“无待恢复状态”并返回 `0`；存在 journal 时要求 Windows 管理员权限或 macOS/Linux `root/sudo` 权限。journal 损坏、属于其他平台、权限不足、恢复失败或恢复校验失败时返回 `5` 并保留现场。

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
python3 coordinator.py --restore
python3 coordinator.py --config ipoedhcp_config.json --log-level INFO
```

`--show` 显示当前 JSON 配置，`--reset` 恢复默认配置，`--interactive` 进入包含手动恢复项的交互菜单。`--capture`、`--extract`、`--dhcp` 可以组合使用；`--pcap` 指定抓包文件，`--duration` 指定抓包秒数。`--restore` 直接透传独立恢复流程，不能与其他主操作组合，也不会读取 `--config` 指向的配置。

### Windows 启动器

Windows 可运行：

```text
run.cmd --show
run.cmd --config ipoedhcp_config.json --interactive
run.cmd --restore
```

启动器优先选择 PowerShell 7，并兼容回退到 Windows PowerShell 5.1。macOS/Linux 可运行 `sudo ./run.sh` 进入统筹器，或运行 `sudo ./run.sh --restore` 手动恢复。

### 恢复命令与自动恢复

恢复同时提供事务自动流程、watchdog 和独立手动命令。手动命令只接受程序按当前平台确定的默认 journal，不提供任意 journal 路径参数。

1. 拨号开始前创建 schema v2 恢复日志；
2. 正常 Stop、`Ctrl+C`、DHCP 失败和可处理异常时恢复网卡；
3. 父进程异常退出时，由 watchdog 读取恢复日志并执行恢复；
4. 断电重启或需要人工重试时，先运行 `python3 ipoedhcp.py --restore`、`run.cmd --restore` 或 `sudo ./run.sh --restore`；
5. 恢复成功后删除日志，恢复失败时保留日志并记录错误，后续启动继续重试。

恢复状态目录：

- Windows：项目 `.temp/network-recovery.json`；
- macOS：`/Library/Application Support/IPoESimulator/network-recovery.json`；
- Linux：`/var/lib/ipoe-simulator/network-recovery.json`。

断电后的建议步骤：

1. 重启后先不要再次抓包或拨号；
2. 使用管理员/root 权限运行对应平台的 `--restore` 命令；
3. 返回 `0` 后人工核对接口 IPv4、默认路由、DNS、DHCP 模式和网络管理器状态；
4. 若返回 `5`，保留默认 journal 和接口现场，根据日志排查后重试，不要删除或手工改写 journal。

## 恢复安全

拨号前创建 schema v2 恢复日志，记录完整接口标识和平台信息。共享事务按 `snapshot → prepare → apply → restore → verify` 执行。恢复流程兼容旧版 Windows schema v1，并对跨平台日志执行平台校验。

Windows watchdog 监听父 PID；macOS/Linux watchdog 监听继承管道。恢复失败时保留日志并记录 `restore_failed`，下一次运行优先处理待恢复日志。

纯 CLI 运行模式提供进程生命周期内的自动恢复能力；整机重新启动后，管理员可通过独立命令根据保留的恢复日志执行恢复。该路径不依赖 Scapy 或 Npcap。

## 致谢

感谢以下开源项目为本项目提供支持：

- [Scapy](https://github.com/secdev/scapy)：用于 DHCP 报文构造、二层收发、抓包和 PCAP/PCAPNG 解析；
- [Python](https://www.python.org/)：提供项目运行时及标准库支持。

上述项目仍归其原作者及维护社区所有，并分别遵循各自的许可证和使用条款。
