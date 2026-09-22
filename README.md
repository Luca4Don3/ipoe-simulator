# IPoE Simulator

IPoE Simulator 是面向授权实验网和维护网的 DHCPv4/IPoE CLI 工具，可替代机顶盒完成接入测试、DHCP 参数验证和故障排查。Windows 10/11 x86、x64、ARM64 是正式支持目标；macOS 和 Linux 仅完成源码适配与离线验证，不提供发布附件。

> 声明：本项目的测试环境基于特定运营商网络；其他地区或运营商环境可能存在差异，使用前请结合实际网络配置进行验证。

当前正式发布版本为 `v0.6.1`。`v0.6.1` 为补丁版本：源码安装的 Scapy 依赖声明改为精确版本 `scapy==2.7.0` 加 SHA-256 哈希，版本号与哈希均与 `release-dependencies.json` 一致，制品内容不变；Windows 发布包仍按锁定清单直接下载固定 URL 的 wheel。`v0.6.0` 引入从明文 ChannelList 提取单播控制端点与租约期内临时主机路由能力。本版本已完成 Windows CI、真实网卡和三架构（x86/x64/ARM64）实机验收。

项目采用 GPL-2.0-only，完整条款见 `LICENSE`。工具不会修改 IPv6，不提供 IPTV 播放、实时 EPG 登录、GUI、开机服务或断电恢复服务。

## 快速开始

源码支持 Python 3.9–3.14，并锁定 Scapy 2.7.0。Windows 要求管理员权限和 Npcap；macOS/Linux 要求 `sudo/root`。先执行只读环境检查：

```text
python3 check_env.py
python3 check_env.py --json
```

普通 Windows 用户应从 GitHub Release 下载与原生架构匹配的 `v0.6.1` 附件，并核对 `SHA256SUMS.txt`。源码运行的基本流程是：

```text
python3 ipoedhcp.py --list-interfaces
python3 ipoedhcp.py --capture-only 30 --interface <interface> --capture-output .temp/stb.pcap
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json
python3 ipoedhcp.py --config ipoedhcp_config.json
```

Windows 可运行 `run.cmd`，macOS/Linux 可运行 `sudo ./run.sh` 进入统筹器。每个平台和架构必须使用匹配的 Python；程序会显式拒绝不支持的版本或架构组合。

## 配置

复制 `config.example.json` 后替换占位符。最小配置为：

```json
{
  "device": {
    "mac": "<mac>",
    "interface": "<interface>"
  }
}
```

常用完整结构为：

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
  "network": {
    "subnet_mask": "",
    "gateway": "",
    "dns": [],
    "unicast_routes": ["<ipv4>"]
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

Option 12/60 是字符串；Option 43/61/125 使用 `0x` 开头的连续十六进制字节；Option 50 是 IPv4 或 4 字节十六进制值。参数应来自你有权使用的抓包或测试资料，不要猜测或复用他人的标识。

`network.unicast_routes` 最多包含 256 个规范化 IPv4 单播地址。只填写地址，不填写 CIDR、网关或 metric。程序在首次 DHCP ACK 后通过 ACK 网关应用对应主机路由；旧配置缺少该字段时等价于空数组。存在单播路由但 ACK 没有网关时，拨号会显式失败并进入恢复。

## 常用操作

直接运行与参数覆盖：

```text
python3 ipoedhcp.py --config ipoedhcp_config.json
python3 ipoedhcp.py --config ipoedhcp_config.json --mac <mac> --interface <interface>
python3 ipoedhcp.py --config ipoedhcp_config.json --option60 ITV-STB --timeout 30
```

提取 PCAP/PCAPNG：

```text
python3 extract_params.py .temp/stb.pcap
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json
python3 extract_params.py .temp/stb.pcap --json ipoedhcp_config.json --stb-mac <mac>
```

使用 `--json` 时，提取结果合并写入指定配置，输出的后续拨号命令也会携带同一路径的 `--config`。不使用 `--json` 时，JSON 结果只打印到控制台；若提取出单播路由，程序会警告它们不会自动进入拨号配置。

ChannelList 处理结果分为：

- 成功解析：以去重后的合法单播端点整体替换 `unicast_routes`，合法空结果会清空旧列表；
- 未发现明文 ChannelList：明确警告并保留配置中的旧列表；
- 检测到但解析不完整、转义损坏、TCP/HTTP 数据不完整或资源超限：返回失败，不写配置。

统筹器支持抓包、提取和拨号的分步或组合执行：

```text
python3 coordinator.py --capture 30 --interface <interface>
python3 coordinator.py --extract .temp/stb.pcap
python3 coordinator.py --dhcp
python3 coordinator.py --interactive
```

## 运行与恢复

程序在修改网卡前保存恢复 journal，正常停止、DHCP 失败和可处理异常都会执行 DHCP Release、恢复网卡并校验结果。正常停止请按一次 `Ctrl+C`；重复中断只报告当前清理阶段，不会重入恢复。

手动恢复命令：

```text
python3 ipoedhcp.py --restore
python3 ipoedhcp.py --restore --log-level DEBUG
python3 coordinator.py --restore
```

Windows 使用 `run.cmd --restore`，macOS/Linux 使用 `sudo ./run.sh --restore`。`--restore` 是独立操作，不读取拨号配置；无 journal 时幂等成功。恢复或校验失败时返回 `5`，保留 journal 和接口现场，并拒绝新的拨号。此时不要删除或编辑 journal，应先检查权限、接口和网络管理状态，再重试恢复并人工核对 IPv4、路由、DNS 与 DHCP 状态。

整机断电不保证执行进程内恢复。重启后应先运行手动恢复命令。已发布版本已完成 Windows 10/11 x86、x64、ARM64 实机验收；后续版本的验收结论以各自发布说明为准。

## 安全限制

仅在你拥有授权的实验网或维护网中运行，并优先使用独立测试网卡。运行期间 IPv4 地址、路由、DNS、DHCP 模式和网卡指标会暂时改变；不要在承载日常办公、生产业务或未知 DHCP 服务的接口上使用。

PCAP、配置、日志和 journal 可能包含 MAC、IP、接口名称、主机名、厂商标识及运营商字段。不要将真实文件提交到公开仓库；分享前应脱敏并限制权限。INFO 日志只记录单播路由数量，DEBUG 日志可能包含具体端点。

测试数据只允许使用 RFC 文档/基准测试保留地址、本地管理 MAC、协议要求的广播或组播常量以及显式占位符。Issue 内容只使用合成数据；安全问题请按 `SECURITY.md` 私下上报，不得携带真实 PCAP、配置、日志或 journal 提交到公开 Issue。

Windows 使用 PowerShell、系统网络 cmdlet 和 Npcap；macOS 使用系统网络工具与 `libpcap/BPF`；Linux 使用系统网络工具、PF_PACKET 和 `tcpdump`。Npcap 不随项目分发，是受其独立许可条款约束的外部运行依赖；其他列出的系统组件同样属于运行环境，不是本项目分发的开源依赖。操作系统断电、内核或网络管理器异常可能超出进程内恢复能力。

## 致谢

本项目感谢以下实际使用的开源工具：

- **Python**：作为项目运行时并提供标准库；来源为 [Python Software Foundation](https://www.python.org/psf/)，采用 [Python Software Foundation License Version 2（PSF License）](https://docs.python.org/3/license.html)。
- **Scapy**：用于 DHCP、二层报文收发以及 PCAP/PCAPNG 读写；来源为 [Scapy 项目（secdev/scapy）](https://github.com/secdev/scapy)，采用 [GPL-2.0-only](https://github.com/secdev/scapy/blob/master/LICENSE)，完整上游许可证文本见 `licenses/SCAPY-LICENSE.txt`。
