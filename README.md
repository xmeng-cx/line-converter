# 线路转换

局域网 PPTP 入站 → Hysteria2 / SOCKS5 出站，Python Web 控制台管理。支持 Armbian ARM64，自动从订阅生成 `vpn1`、`vpn2`…，统一密码 `123456`。

## 一键安装

仓库当前为私有。在 **Armbian / Debian / Ubuntu 的 root 终端**安装 GitHub CLI 并登录有仓库权限的账号（首次需要）：

```bash
apt-get update && apt-get install -y gh
gh auth login
```

然后一键安装：

```bash
gh api repos/xmeng-cx/line-converter/contents/bootstrap.sh -H 'Accept: application/vnd.github.raw' | bash
```

如将仓库改为公开，可直接免登录安装：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/xmeng-cx/line-converter/main/bootstrap.sh)
```

指定局域网 IP（多网卡、安装了其他 TUN 代理时建议指定）：

```bash
export LINE_LAN_IP=192.168.1.144
gh api repos/xmeng-cx/line-converter/contents/bootstrap.sh -H 'Accept: application/vnd.github.raw' | bash
```

安装后打开 `http://设备局域网IP:8088`，添加订阅，点击 **解析预览 → 应用线路**。安装会编译 accel-ppp，并下载独立的 sing-box，首次安装需要几分钟。

也可以下载代码后执行：

```bash
gh repo clone xmeng-cx/line-converter
cd line-converter
sudo bash install.sh
```

需要内核支持 PPP、PPTP、TPROXY，安装脚本会通过 `modprobe` 检查。支持 ARM64、ARMv7、x86_64；实际部署验证平台为 Armbian ARM64。需要能访问软件源及 GitHub；IPv6 订阅/节点还需要可用的 IPv6 网络。

## 使用方法

1. 添加 HTTP/HTTPS 订阅地址，兼容 `http://[IPv6]:端口/路径`。
2. 预览识别到的 HY2 / SOCKS5 线路以及新增账号数量，再应用。
3. 客户端创建 PPTP VPN：服务器填写设备局域网 IP，用户名 `vpn1`，密码 `123456`。每个账号绑定一个固定拨号 IP 和一个出站。
4. 控制台可更换账号线路、启停账号、断开会话、检测节点 HTTPS 连通性、导出 CSV。
5. “更换订阅”可修改地址或自动刷新周期；“更新线路”立即刷新。

客户端需要支持 PPTP，且允许未加密连接（Windows 可选择“可选加密”）。MPPE 不强制启用。PPTP 使用 TCP 1723 和 GRE（IP 协议号 47，**不是 UDP/TCP 端口 47**）。服务器只绑定配置的局域网 IPv4，默认允许该 IP 所在 `/24` 网段拨入。

## 更新规则

| 订阅变化 | 账号行为 |
| --- | --- |
| 顺序变化、密码变化 | 保留原账号，通过节点身份匹配更新 |
| 节点地址变化但名称唯一且相同 | 保留账号与对应关系 |
| 相同数量的线路被全部替换 | 使用现有账号，更新绑定 |
| 线路增加 | 优先填补该订阅的空缺账号，然后递增新增 |
| 线路减少 | 保留账号，失效线路停止转发，不删除、不重新编号 |
| 下载或解析失败 | 保留原配置 |
| 手动更换过绑定 | 普通刷新保留手动选择，除非该节点已被删除 |

账号、密码和拨号 IP 保持稳定。节点不存在可靠唯一标识时，无法保证“改名且换地址”后仍对应原地区，会依次填补空缺账号。应用配置会重启项目的转发核心，已有 TCP/UDP 连接可能中断；PPTP 服务不因普通订阅更新而整体重启。

## 支持的订阅

订阅中的 `DIRECT` 规则会一同导入，先于账号默认代理线路匹配，从 Armbian 本机网络出口直连。规则按账号所属订阅隔离；停用或无可用线路的账号仍拒绝转发。刷新订阅会替换规则，删除的规则不继续生效，账号和密码不变。控制台显示直连规则数量、明细及不支持的规则提示。

支持 Clash 的 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`IP-CIDR`、`IP-CIDR6`、`MATCH,DIRECT`，保留 IP 规则的 `no-resolve`；支持 sing-box 指向 direct 出站的基本域名/IP/端口/网络规则。复杂逻辑、GEOIP/GEOSITE、外部 RULE-SET 暂不展开，会显示提示。仅导入直连规则，其他策略仍使用账号所绑定的线路。

直连域名通过独立 DNS 解析（默认 `223.5.5.5`，可在 `/etc/line-converter.env` 设置 `LINE_DIRECT_DNS`）。A/AAAA 继续使用 FakeIP 保持域名识别，命中直连后由本机解析连接。直连出站使用专用路由标记绕过现有 TUN 代理。订阅中允许的内网、环回地址也按其直连规则生效。

- 原始 `hy2://`、`hysteria2://`、`socks5://`、`socks://` 链接列表。
- Base64 编码链接列表。
- Clash YAML 的 `proxies`。
- sing-box JSON 的 `outbounds`。
- HY2 的 SNI、认证密码、证书校验设置、Salamander 混淆。

不支持的协议会显示跳过原因，不为其生成账号。当前不支持端口跳跃、SOCKS over TLS、复杂自定义 TLS/传输扩展。订阅上限 4 MB / 2000 条；账号地址池 `10.66.0.0/16`。PPTP 客户端侧为 IPv4，出站节点服务器可以是 IPv4、IPv6 或域名。

TCP 和 UDP 经过所选出站。PPTP 客户端的端口 53 DNS 查询被接管，A/AAAA 使用独立 FakeIP 地址池 `198.19.0.0/16`，实际访问时恢复域名并交由出站处理；其他 DNS 查询通过账号绑定节点的 DoH 处理。这样 IPv4 PPTP 客户端也可通过 IPv6 出口访问双栈网站。使用客户端自带 DoH、已有 DNS 缓存或直接连接 IPv4 地址时无法进行此转换，IPv6-only 上游仍不能访问 IPv4-only 目标。上游 SOCKS5 必须支持 UDP ASSOCIATE 才能承载普通 UDP。未匹配、失效、停用账号默认拒绝转发；不自动回退为本机直连。ICMP 等其他协议不转发，所以不要只用 ping 判断代理是否可用。

## 架构和部署

```text
PPTP 客户端 → accel-ppp (lc* 接口)
                     ↓ TPROXY
            sing-box 按拨号 IP 路由 → HY2 / SOCKS5
                     ↑
          Python 控制台 / SQLite / 订阅解析
```

采用独立 sing-box 核心以兼容常见订阅中的 HY2 证书校验参数。不会覆盖设备已有的 Xray / Mihomo 配置。项目专用 SO_MARK 和策略路由使出站连接绕过其他 TUN；仅 `lc*` 接口进入透明代理。

| 路径 / 服务 | 用途 |
| --- | --- |
| `/opt/line-converter` | 程序 |
| `/opt/line-converter/data` | SQLite、订阅地址、生成配置、日志，root 专用 |
| `/etc/line-converter.env` | 监听地址、运行参数 |
| `line-converter` | Python 控制台和定时刷新 |
| `line-converter-core` | 独立 sing-box |
| `line-converter-pptp` | 独立 accel-ppp |
| `line-converter-network` | 项目专用防火墙与路由 |

使用端口 8088（局域网控制台）、1723（PPTP）、16600（透明代理，仅接收标记流量）、16611（仅本机的会话管理）；使用策略路由优先级 166–168、表 166、标记 16601/16602。不要与其他服务重复使用这些资源。

```bash
systemctl status line-converter line-converter-core line-converter-pptp
journalctl -u line-converter -u line-converter-core -n 80 --no-pager
tail -n 80 /opt/line-converter/data/accel.log
```

重新运行安装命令可更新程序，保留 `data` 中的账号与订阅。修改设备 IP 后重新指定 `LINE_LAN_IP` 运行安装，并重启 `line-converter-pptp`。开机自动启动，异常退出由 systemd 拉起。

备份时停止控制台并备份整个 data 目录；恢复后重启控制台。不要将 data 目录、订阅令牌或 SSH 凭据提交到 GitHub。本项目不保存部署用的 SSH 凭据。

## 本地开发

Python 3.10+：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -p 'test_*.py' -v
python app.py
```

默认是本地预览模式：`http://127.0.0.1:8088`，不会更改主机网络或启动系统服务；实际线路检测需要 Linux 部署的核心。真实部署由环境变量 `LINE_LIVE=1` 启用。

控制台按可信局域网设计，不含公网登录系统，不要映射到公网。HY2 保留协议自带 TLS；是否跳过节点证书校验遵循订阅设置。

## 验证记录

在 Armbian ARM64 上完成：

- 真实 IPv6 订阅导入 10 条 HY2 线路，自动生成 10 个固定账号。
- 10 个节点逐个完成 HTTPS 出口 IP 检测。
- 隔离网络命名空间中的真实 PPTP 拨号，CHAP 认证、固定 IP 分配成功。
- 通过 PPTP 的 DNS / HTTPS 请求确认出口为绑定 HY2 节点。
- 临时 SOCKS5 测试节点验证 PPTP → SOCKS5 → HY2 链路成功；测试后恢复原配置。
- 真实订阅刷新后账号和绑定完全一致；另有单元测试覆盖增减、替换、重排、手动绑定保留与无效输入。
- Web 控制台账号列表与在线会话实际浏览器验证。

未进行大规模并发压力测试；测试用的 PPTP 客户端位于服务器隔离命名空间，实际客户端和局域网设备的 GRE 放行情况仍取决于现场网络。
