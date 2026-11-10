# 桌面端 Bridge 服务

Bridge 在**内部端口**上提供 MCP 端点，把客户端请求转发到**独立核心**。
接入 Bridge 的客户端（C#、Electron、PySide6 或任意 MCP 客户端）不再随包携带核心代码，
核心可以单独升级，客户端只负责交互。

> 当前桌面端（`davinci-gw-gui.exe`）仍在进程内直接调用 `GatewayFacade`，
> **尚未接入 Bridge**。Bridge 先作为可选通道落地：桌面端接入之前，
> 升级 Bridge 核心不会改变桌面端的行为。

Bridge 不实现任何路由规则，也不改变现有 MCP 服务的边界：转发目标的行为完全由核心决定。

## 为什么要 Bridge

当前桌面端把核心代码随免安装包分发、在进程内直接调用 `GatewayFacade`。
MCP 版自迭代产出的修复（`hotfix/mcp-auto-*` → 合并 `main`）只进入源码仓库，
同事手上的桌面端仍然是修复前的代码。Bridge 为此提供解耦通道：
下面的链路要等桌面端（或新客户端）改为经 Bridge 调用核心之后才真正生效。

```text
桌面客户端（只做交互）
        │  MCP over Streamable HTTP，Bearer 令牌
        ▼
davinci-gw-bridge（127.0.0.1:<端口>，校验令牌、转发）
        │  进程内 / STDIO / HTTP
        ▼
独立核心（唯一一份业务逻辑，可单独升级）
```

## 启动

```powershell
# 同进程核心：最简单，核心随桥接进程一起启动
python -m davinci_gw.bridge --core embedded

# 独立核心进程：核心可单独升级，桥接只做转发
python -m davinci_gw.bridge --core stdio --core-command "C:\tools\davinci-gw-mcp.exe"

# 独立核心进程（源码运行）
python -m davinci_gw.bridge --core stdio --core-command "C:\Python\python.exe -m davinci_gw.mcp"

# 内网核心服务：核心部署在另一台机器，客户端与桥接都只做客户端
python -m davinci_gw.bridge --core http --core-target http://127.0.0.1:9100/mcp --core-token <令牌>
```

命令行参数（全部选项）：

| 参数 | 说明 |
| --- | --- |
| `--core` | `embedded` / `stdio` / `http` |
| `--core-command` | `stdio` 模式的核心完整命令行，路径含空格时用引号 |
| `--core-target` | `stdio` 模式为可执行文件路径；`http` 模式为 MCP 端点 URL |
| `--core-arg` | 追加的核心参数；参数以 `-` 开头时使用 `--core-arg=-m` 形式 |
| `--core-env` | `stdio` 模式核心进程的环境变量，`KEY=VALUE` 形式，可重复 |
| `--core-token` | `http` 模式访问核心所需的令牌 |
| `--core-timeout` | 单次核心调用的超时秒数，缺省 3600；超时按核心不可用处理 |
| `--host` / `--port` / `--path` | 监听地址、端口（`0` 表示系统分配）、端点路径 |
| `--token` | 桥接访问令牌；缺省时在启动时随机生成一次 |
| `--descriptor` | 客户端发现文件路径，缺省为 `%LOCALAPPDATA%\DaVinciGW\bridge.json` |
| `--no-descriptor` | 不写发现文件；此时必须显式提供 `--token`，否则客户端无从获取令牌 |
| `--allow-remote` | 允许非回环监听；必须显式开启 |
| `--tls-certfile` / `--tls-keyfile` | 对外监听时的 TLS 证书与私钥路径；两者必须同时提供 |
| `--allow-remote-insecure` | 对外监听时不启用 TLS；仅在端口已置于受信加密隧道之后时使用 |
| `--allowed-host` | 客户端实际使用的主机名或 IP，可重复；用于 Host 白名单 |
| `--public-host` | 对外通告的客户端可达主机名或 IP；绑定全部接口时用它替代 `0.0.0.0` |
| `--log-level` | 日志级别 |

`--core-env` 只对 `stdio` 核心生效，按 `KEY=VALUE` 给出，可重复传入。
注意核心子进程拿到的**不是**父进程的完整环境：SDK 只继承一小组安全变量
（Windows 下为 `APPDATA`、`LOCALAPPDATA`、`PATH`、`PATHEXT`、`SYSTEMROOT`、
`TEMP`、`USERNAME`、`USERPROFILE` 等），其余变量不会传下去。核心需要的
自定义变量、自定义证书路径、代理设置等，都要用 `--core-env` 显式补上：

```powershell
python -m davinci_gw.bridge --core stdio `
  --core-command "C:\Python\python.exe -m davinci_gw.mcp" `
  --core-env REQUESTS_CA_BUNDLE=C:\certs\corp.pem `
  --core-env HTTP_PROXY=http://proxy.intra:8080
```

### 对外监听必须加密

`--allow-remote` 之后，令牌与路由结果会跨主机传输。默认仍走明文 HTTP，
等于把访问凭据交给链路上的旁观者，因此**非回环监听必须启用 TLS**：

```powershell
python -m davinci_gw.bridge --core embedded --host 0.0.0.0 --allow-remote `
  --tls-certfile C:\certs\bridge.crt --tls-keyfile C:\certs\bridge.key
```

启用证书后，发现文件与能力接口里的地址自动变为 `https://`。
如果端口本身就位于 VPN、SSH 隧道或 mTLS 网关之后，等价于已加密，
这时可以用 `--allow-remote-insecure` 显式声明；桥接会在启动日志中打印
明文传输警告，提醒运维复核。回环监听不受此限制。

### 客户端主机名与 Host 白名单

对外监听时，客户端通常用机器的可路由 IP 或域名访问，而不是监听地址本身。
Host 白名单无法枚举这些名字，需要显式登记：

```powershell
python -m davinci_gw.bridge --core embedded --host 0.0.0.0 --allow-remote `
  --tls-certfile C:\certs\bridge.crt --tls-keyfile C:\certs\bridge.key `
  --allowed-host 192.168.1.10 --allowed-host gateway.internal
```

绑定到全部接口且没有登记任何主机名时，Host 白名单无从枚举，桥接会**关闭
Host 校验**并在日志中给出警告：此时访问控制完全由 Bearer 令牌与传输加密
承担。登记了主机名则照常开启校验。

### 对外通告的地址

`--host 0.0.0.0` 是监听地址，不是客户端能连接的地址：把它写进发现文件
和能力接口，客户端会连到"未指定地址"上去。绑定全部接口时用
`--public-host` 指定客户端实际可达的主机名或 IP：

```powershell
python -m davinci_gw.bridge --core embedded --host 0.0.0.0 --allow-remote `
  --tls-certfile C:\certs\bridge.crt --tls-keyfile C:\certs\bridge.key `
  --public-host 192.168.1.10 --allowed-host 192.168.1.10
```

发现文件与能力接口里的地址都使用这个主机名，它也会自动进入 Host 白名单。
没有指定时退回回环地址并在启动日志中提示远端客户端不可达。
IPv6 监听地址（如 `2001:db8::1`）在地址与白名单里都会带方括号，
与 HTTP `Host` 头的写法一致。

安装后也可以直接使用 `davinci-gw-bridge` 命令。

## 客户端发现文件

桥接启动后写入一份 JSON，供客户端读取端口与令牌：

```json
{
  "schema_version": "1.0",
  "name": "davinci-gw-bridge",
  "version": "1.3.6",
  "pid": 1234,
  "host": "127.0.0.1",
  "port": 8765,
  "path": "/mcp",
  "url": "http://127.0.0.1:8765/mcp",
  "token": "……",
  "core": { "mode": "embedded", "target": null, "args": [] },
  "started_at": "2026-11-03T08:00:00+00:00"
}
```

`version` 与核心版本一致，由 `davinci_gw.__version__` 提供。

文件含明文令牌，只应由当前用户可读；不需要客户端发现时可加 `--no-descriptor`。
加了 `--no-descriptor` 就必须同时用 `--token` 显式指定令牌：缺省生成的随机令牌
只写在发现文件里，关掉发现文件后客户端就没有任何途径拿到它，调用会一律 401。

令牌在**进程启动时确定一次**：缺省时随机生成，并在整个进程生命周期内保持不变。发现文件里的 `token` 与服务端鉴权使用的是同一个值，客户端直接读取该字段即可，不会出现“发现文件与实际校验令牌不一致”导致的 401。

## 对外工具

Bridge 只暴露四个网关工具，参数与语义与 MCP STDIO 服务完全一致：

| 工具 | 读写 | 说明 |
| --- | --- | --- |
| `get_gateway_capabilities` | 只读 | 核心版本、路由能力、Prepared Session 与路径安全策略；额外返回桥接端点与核心形态 |
| `validate_gateway_inputs` | 只读 | 校验 `.xlsx` 配置表与 `.arxml` 基准 |
| `preview_gateway_update` | 只读 | 预览变更并创建一次性 `preparation_id` |
| `generate_gateway_arxml` | 写 | 使用 `preparation_id` 原子生成新的 `.arxml`，禁止覆盖 |

调用顺序固定为 `validate → preview → 展示并确认 → generate`。

### 有意不暴露的工具

自动修复系列（`diagnose_generation_failure`、`start_bug_repair`、`get_bug_repair_status`、
`submit_bug_repair`、`cancel_bug_repair`）**不在桥接上开放**：

- 它们只在保留完整源码与 Git 仓库的开发机上可用（见 [MCP 本地安装与使用](MCP本地安装与使用.md)）；
- 诊断与修复工具会按客户端传入的 `repository_path`、`python_path` 启动子进程，
  放到监听端口上等于允许远程调用方驱动本机命令。

需要这些能力时，直接使用 `davinci-gw-mcp` 的 STDIO 服务。

## 安全边界

| 措施 | 说明 |
| --- | --- |
| 回环监听 | 默认只绑定 `127.0.0.1`；非回环地址必须显式 `--allow-remote`，否则拒绝启动 |
| 传输加密 | 非回环监听必须启用 TLS（`--tls-certfile` + `--tls-keyfile`）；确已在受信隧道之后时用 `--allow-remote-insecure` 显式声明，并打印明文警告 |
| 令牌鉴权 | 所有请求必须携带 `Authorization: Bearer <token>`；缺失或不匹配一律 401 |
| DNS 重绑定防护 | 启用 SDK 的 `TransportSecuritySettings`，校验 `Host`/`Origin` 白名单；对外监听时用 `--allowed-host` 登记客户端实际使用的主机名 |
| 可达地址通告 | 绑定全部接口时用 `--public-host` 指定客户端可达的主机名，发现文件与能力接口不再通告 `0.0.0.0` |
| 凭据脱敏 | 能力接口与发现文件中的核心描述不含令牌；核心目标里的账号密码与参数里的令牌、密钥类取值一律替换为 `***` |
| 能力清单一致 | 能力接口的工作流只列桥接实际注册的路由工具，不会把核心的修复系列工具一并广告出去 |
| 路径安全 | 不自行解析路径；输入输出校验全部由核心执行（只接受带盘符的本地绝对路径，拒绝 UNC、设备路径与覆盖已有文件） |
| 响应上限 | 沿用核心的有界信封：错误优先、最多 200 条问题、总响应不超过 512000 字节 |
| 失败隔离 | 核心不可用时返回 `INTERNAL_FAILURE` 与 `BRIDGE_CORE_UNAVAILABLE`，不泄露异常细节与堆栈 |

令牌以 `hmac.compare_digest` 做常数时间比较。

鉴权失败时，桥接会**先读完请求体再回 401**。若在客户端还在发送 body 时就直接关闭连接，Windows 网络栈会用 RST 中断连接，调用方收到的是传输层错误而不是 HTTP 状态码。排空请求体保证客户端总能读到完整的 401 响应。排空有整体时限（5 秒）：对端发了一部分 body 就停住且不关闭连接时，不会一直占着任务与套接字。

核心会话建立失败后，桥接会清掉这次失败的状态：下一次调用会重新建立会话，
而不是永远复用一个注定失败的任务、只能靠重启 Bridge 恢复。

核心调用超时后，桥接会**立即**返回核心不可用，不等核心把正在做的活干完。
但核心内部的重任务仍按串行方式执行：它会占用核心的执行槽位直到到达安全
边界（真正的 ARXML 解析没有中途取消点，要等它自己返回）。这段时间内，
发往同一个核心的新请求会排队等待，同样受各自的 `--core-timeout` 约束。
这是有意为之——并发跑第二个重任务会争抢同一个工作线程，反而更慢。

端口为 `0` 时由系统分配实际端口，发现文件与能力接口里的地址都使用这个
实际端口，客户端拿到的端点可以直接连接。

## 客户端示例

Python（官方 MCP SDK）：

```python
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

http_client = create_mcp_http_client(headers={"Authorization": "Bearer <token>"})
async with Client(streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http_client)) as client:
    result = await client.call_tool("get_gateway_capabilities")
```

C# 与 Electron 使用各自的官方 MCP SDK，连接同一个 Streamable HTTP 端点并携带
`Authorization` 头即可，无需了解桥接内部实现。

## 边界与限制

- **Prepared Session 属于核心进程**。`preparation_id` 在核心进程内创建并消费，
  核心重启后失效；Bridge 重启但核心不变时，客户端持有的 `preparation_id` 仍然有效。
- **Bridge 不改变会话语义**：默认 15 分钟过期、每进程最多 8 个会话、
  生成成功即消费，全部沿用核心行为。
- **端口不是必需的**。MCP 服务本身是 STDIO 传输，Bridge 是可选组件；
  不需要端口的部署可以直接使用 `davinci-gw-mcp`。
- **尚未提供发布包**。当前只提供源码运行入口；`packaging/` 下的免安装 ZIP 与
  `scripts/setup.ps1` 尚未接入 Bridge，后续按 MCP 包的既有模式补齐。
- **同机单客户端**：Bridge 面向本机桌面客户端设计，不做多租户与并发账号隔离。

## 相关文档

- 《桌面前端架构改造评估报告》：换栈与否的评估与三阶段路线（评审中，尚未合入）
- [MCP 本地安装与使用](MCP本地安装与使用.md)：STDIO 服务的安装、诊断与自动修复
- [GUI 使用说明](GUI.md)：现有桌面端使用说明
