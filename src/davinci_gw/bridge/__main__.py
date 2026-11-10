"""桥接服务命令行入口。

用法示例::

    python -m davinci_gw.bridge --core embedded
    python -m davinci_gw.bridge --core stdio --core-target "C:\\tools\\davinci-gw-mcp.exe"
    python -m davinci_gw.bridge --core http --core-target http://127.0.0.1:9100/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from davinci_gw.bridge.config import BridgeConfig, BridgeConfigError, CoreConfig, is_loopback_host
from davinci_gw.bridge.core import build_core
from davinci_gw.bridge.server import BRIDGE_VERSION, build_bridge_app, build_bridge_server

LOGGER = logging.getLogger("davinci_gw.bridge")

DEFAULT_DESCRIPTOR_NAME = "bridge.json"

# 服务收尾的时限（秒）：发现文件写不出去时不能无限期挂住。
SHUTDOWN_TIMEOUT_SECONDS = 10.0


def _split_command(command: str) -> tuple[str, list[str]]:
    """把整串命令行拆成可执行文件与参数，保留 Windows 路径中的反斜杠。"""
    tokens = [token.strip('"') for token in shlex.split(command, posix=False)]
    tokens = [token for token in tokens if token]
    if not tokens:
        raise BridgeConfigError("核心命令行不能为空。")
    return tokens[0], tokens[1:]


def _parse_env(items: Sequence[str]) -> dict[str, str]:
    """解析 KEY=VALUE 形式的核心环境变量。"""
    environment: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise BridgeConfigError(f"核心环境变量格式无效：{item}")
        environment[name] = value
    return environment


def _default_descriptor_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "DaVinciGW" / DEFAULT_DESCRIPTOR_NAME


def _discovery_token_error(no_descriptor: bool, token: str | None) -> str | None:
    """关掉发现文件却不给令牌时返回错误说明。

    令牌缺省时随机生成，只写在发现文件里；关掉发现文件就等于把令牌
    藏了起来：服务能起来，但客户端无从构造 Authorization 头，每一次
    调用都会鉴权失败。
    """
    if no_descriptor and not token:
        return "使用 --no-descriptor 时必须显式提供 --token；随机生成的令牌没有其他可交付的途径。"
    return None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="davinci-gw-bridge", description="DaVinci 网关路由桥接服务")
    parser.add_argument("--core", choices=("embedded", "stdio", "http"), default="embedded",
                        help="核心形态：embedded=进程内，stdio=独立子进程，http=内网服务")
    parser.add_argument("--core-command", default=None,
                        help="stdio 核心的完整命令行，例如 \"C:\\path\\python.exe -m davinci_gw.mcp\"")
    parser.add_argument("--core-target", default=None, help="核心目标：stdio 模式为可执行文件，http 模式为 URL")
    parser.add_argument("--core-arg", action="append", default=[],
                        help="stdio 模式的核心参数；参数以 - 开头时使用 --core-arg=-m 形式")
    parser.add_argument("--core-env", action="append", default=[], metavar="KEY=VALUE",
                        help="stdio 模式核心进程的环境变量，可重复")
    parser.add_argument("--core-token", default=None, help="http 模式访问核心所需的令牌")
    parser.add_argument("--core-timeout", type=float, default=3600.0, help="核心调用超时秒数")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机可达")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，0 表示由系统分配")
    parser.add_argument("--path", default="/mcp", help="MCP 端点路径")
    parser.add_argument("--token", default=None, help="桥接访问令牌；缺省时随机生成")
    parser.add_argument("--descriptor", default=None, help="客户端发现文件路径")
    parser.add_argument("--no-descriptor", action="store_true", help="不写客户端发现文件")
    parser.add_argument("--allow-remote", action="store_true", help="允许非回环监听；必须显式开启")
    parser.add_argument("--tls-certfile", default=None, help="对外监听时的 TLS 证书路径")
    parser.add_argument("--tls-keyfile", default=None, help="对外监听时的 TLS 私钥路径")
    parser.add_argument("--allow-remote-insecure", action="store_true",
                        help="对外监听时不启用 TLS；仅在端口已置于受信加密隧道之后时使用")
    parser.add_argument("--allowed-host", action="append", default=[], metavar="HOST",
                        help="客户端实际使用的主机名或 IP，可重复；绑定全部接口时用于 Host 白名单")
    parser.add_argument("--public-host", default=None,
                        help="对外通告的客户端可达主机名或 IP；绑定全部接口时用它替代 0.0.0.0")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser.parse_args(argv)


def _bound_port(server: Any, fallback: int) -> int:
    """读取实际绑定的端口；端口为 0 时由系统分配。"""
    for http_server in getattr(server, "servers", []) or []:
        for sock in getattr(http_server, "sockets", []) or []:
            try:
                return int(sock.getsockname()[1])
            except OSError:  # pragma: no cover - 极端平台差异
                continue
    return fallback


def _restrict_descriptor_permissions(path: Path) -> None:
    """把发现文件限制为仅当前用户可读。

    发现文件里写着明文访问令牌，别的本地账号能读到就等于拿到了
    Bridge 的调用权限。

    ``os.chmod(path, 0o600)`` 在 Windows 上只改只读属性，不建立
    owner-only 的 DACL——实测改完之后文件仍是 0o666，ACL 里其它用户
    照样能读。Windows 上改为用 ``icacls`` 重建访问控制表，三步走：

    1. ``/reset``：把访问控制表重置为父目录的默认继承项，
       文件上**已有**的显式授权（例如 ``Everyone:(R)``）一并清掉；
    2. ``/inheritance:r``：把继承项摘掉，剩下空表；
    3. ``/grant:r *SID:(F)``：只给当前用户一条完全控制。

    只做 ``icacls /grant:r`` 与 ``/inheritance:r`` 是不够的：那两步
    只替换当前用户的授权并去掉继承项，文件上**已有**的显式授权
    会原样留下，别的账号照样能读到令牌。

    这里刻意**不解析** ``icacls`` 的文本输出去逐条摘授权：输出是
    本地代码页，长路径或中文路径下会乱码并折行，按行切分会把
    续行误当成一个主体，``/remove:g`` 就会失败，fail-closed 之下
    Bridge 反而起不来。``/reset`` 不需要知道其它主体是谁。

    顺序上有两点讲究：

    - ``/reset`` 必须在 ``/inheritance:r`` 之前。``/reset`` 把访问
      控制表重置为父目录的默认继承项，文件上原有的显式授权随之清掉；
      紧接着 ``/inheritance:r`` 把继承项也摘掉，访问控制表就变成空的
      （谁都没有权限），最后 ``/grant:r`` 只补当前用户一条完全控制。
    - 这三步只作用在**临时文件**上（见 ``_write_descriptor``），
      因此中间那段"谁都没有权限"的空窗不影响客户端：目标文件在整个
      过程中原封不动，客户端读到的始终是权限完好的旧文件或新文件。
      若把这三步直接用在正在被读取的目标文件上，空窗会让客户端撞上
      ``PermissionError``——实测 20 轮里读失败一万五千余次。

    这里不用 PowerShell 的 ``Set-Acl``：``Set-Acl`` 属于
    ``Microsoft.PowerShell.Security`` 模块，在 PowerShell 7 环境里
    启动的 Windows PowerShell 5.1 会因模块自动加载失败而报错
    （"the module could not be loaded"）；而且它带 ``SetOwner`` 时
    需要 ``SeSecurityPrivilege``，对同一个文件重复收紧会失败，
    Bridge 重启就会因 fail-closed 直接起不来。``icacls`` 是系统自带
    的可执行文件，没有模块依赖，重复执行结果一致。

    收紧失败必须让启动失败：把明文令牌写进一个没保护的文件，
    比不写发现文件更危险。
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - 权限受限的环境
            pass
        return
    sid = _current_user_sid()
    if not sid.upper().startswith("S-1-"):
        # 取不到 SID 就无法精确授权；授权给用户名可能命中域内同名账户。
        raise BridgeConfigError("无法确定当前用户的 SID，不能安全地限制发现文件的访问控制表。")
    # 先重置为默认继承项：这一步会把文件上已有的显式授权一并清掉。
    _run_icacls(path, "/reset")
    # 再把继承项摘掉，访问控制表变成空的（谁都没有权限）。
    _run_icacls(path, "/inheritance:r")
    # 最后只给当前用户一条完全控制。
    # 授权项必须写成 *SID：icacls 把不带 * 的 "S-1-…" 当账户名解析，
    # 会报 "No mapping between account names and security IDs"。
    _run_icacls(path, "/grant:r", f"*{sid}:(F)")


def _run_icacls(path: Path, *arguments: str) -> None:
    """执行一条 icacls 命令，失败即抛出可读的 BridgeConfigError。"""
    import subprocess

    try:
        completed = subprocess.run(
            ["icacls", str(path), *arguments], capture_output=True, timeout=30,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 取决于系统环境
        raise BridgeConfigError(
            f"无法收紧发现文件的访问控制表，发现文件可能对其它本地账号可读：{exc}"
        ) from exc
    if completed.returncode != 0:
        raise BridgeConfigError(
            "无法收紧发现文件的访问控制表，发现文件可能对其它本地账号可读："
            + (completed.stdout + completed.stderr).strip()[-300:]
        )


def _current_user_sid() -> str:
    """取当前用户的 SID，用于精确授权。

    ``whoami /user /fo csv /nh`` 的输出形如
    ``"DESKTOP\\user","S-1-5-21-..."``（两列，不是四列）。SID 才是
    唯一可靠的账户标识：裸用户名在域内存在同名账户时会授权给错的人。
    """
    try:
        import subprocess

        completed = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in completed.stdout.splitlines():
            fields = [item.strip('" ') for item in line.split(",")]
            for field in fields:
                if field.upper().startswith("S-1-"):
                    return field
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 取决于系统环境
        pass
    import getpass

    try:
        return getpass.getuser()
    except OSError:  # pragma: no cover - 取不到用户名时
        # 连用户名都取不到时，交给 icacls 自己解析当前账户；
        # 这里给一个必然解析失败的占位，让授权明确失败而不是授权给错人。
        return ""


def _write_descriptor(path: Path, descriptor: dict[str, object]) -> None:
    """写出客户端发现文件，并确保只有当前用户能读。

    发现文件里有明文令牌，落盘时必须已经是 owner-only 的权限：直接
    ``write_text`` 会让文件先按父目录继承的权限落盘，写入与收紧之间
    存在一个明文令牌可被其它本地账号读取的窗口。

    因此顺序固定为"先建空文件 → 收紧权限 → 写入内容"。收紧发生在
    内容写入之前，磁盘上任何时刻都不存在"已带明文、但还没收紧"的
    文件。这条在 Windows 与 POSIX 上都成立。

    Windows 上采用"受限临时文件 + 原子替换"：在同目录建一个空临时
    文件，先把它的访问控制表收紧，再写入内容，最后 ``os.replace``
    换到目标路径。收紧动作只作用在**临时文件**上，目标文件的访问
    控制表在整个过程里原封不动——既有发现文件已经被收紧过时，再对
    它执行 ``icacls`` 会短暂出现"谁都没有权限"的空窗，客户端在这个
    窗口里读文件会拿到 ``PermissionError``；而先删后建则会在客户端
    正持有文件句柄时删不掉（``WinError 32``）。原子替换两条路都绕开了。

    ``os.replace`` 在同目录内是原子的，客户端读到的要么是旧文件、
    要么是完整的受限新文件，不会读到写了一半的内容。

    唯一例外是目标文件正被客户端独占打开：Windows 的 rename 要求
    目标没有打开的句柄，此时替换会报 ``WinError 5``。这种情况
    重试一小段时间即可——客户端读完就关闭句柄，通常只是几十毫秒。

    目标文件带只读属性时替换同样会失败（``WinError 5``），替换前
    先把只读属性去掉：发现文件由程序自己维护，用户手动设的只读
    不该让 Bridge 起不来。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(descriptor, ensure_ascii=False, indent=2)
    if os.name != "nt":
        # POSIX：先以 owner-only 权限把文件建出来，再写入内容。
        # os.open 的 mode 只作用于新建文件，已存在的文件要另外 chmod。
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(handle)
        _restrict_descriptor_permissions(path)
        path.write_text(content, encoding="utf-8")
        return
    temporary = path.with_name(path.name + ".tmp")
    try:
        if temporary.exists():
            temporary.unlink()
    except OSError:  # pragma: no cover - 残留临时文件删不掉时覆盖写入
        pass
    try:
        # 先建空文件并收紧，再写入内容：明文令牌落盘时文件已经是
        # owner-only，不存在"已落盘但未收紧"的窗口。
        temporary.touch(exist_ok=True)
        _restrict_descriptor_permissions(temporary)
        temporary.write_text(content, encoding="utf-8")
    except (OSError, BridgeConfigError) as exc:
        raise BridgeConfigError(
            f"无法写入客户端发现文件 {path}：{exc}"
        ) from exc
    try:
        _clear_readonly_attribute(path)
        _replace_with_retry(temporary, path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:  # pragma: no cover - 清理失败不影响主流程
            pass


def _clear_readonly_attribute(path: Path) -> None:
    """去掉目标文件上的只读属性。

    Windows 上带只读属性的文件不能被替换（``MoveFileEx`` 报
    ``WinError 5``）。发现文件由程序自己维护，用户手动设的只读
    不应该让 Bridge 起不来，因此替换前先清掉它。文件不存在或本来
    就不是只读时什么都不做。
    """
    try:
        if path.exists() and not os.access(path, os.W_OK):
            os.chmod(path, 0o666)
    except OSError:  # pragma: no cover - 清不掉时留给替换阶段报错
        pass


def _replace_with_retry(temporary: Path, path: Path, attempts: int = 20) -> None:
    """把临时文件换到目标路径；目标正被占用时短暂重试。

    Windows 的 ``MoveFileEx`` 要求目标文件没有打开的句柄，客户端正
    在读发现文件时替换会报 ``WinError 5``。客户端读完即关，通常
    几十毫秒内就能换上，因此重试一小段时间即可。
    """
    import time

    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
        except OSError as exc:  # pragma: no cover - 其它错误直接失败
            last_error = exc
            break
    raise BridgeConfigError(
        f"无法替换客户端发现文件 {path}：{last_error}"
    ) from last_error


def _startup_failure_message(error: BaseException | None) -> str:
    """把服务启动阶段的异常翻译成一句能看懂的说明。

    Uvicorn 绑定端口失败时会抛 ``SystemExit(3)``——它不携带任何
    可读信息，真正的原因（端口被占用之类）藏在它内部的
    ``__cause__`` 里。直接打印 SystemExit 只会得到一个没有意义的
    「3」，因此这里往下一层取真正的原因。

    SystemExit 那条分支目前实际走不到（asyncio 会先终止事件循环，
    端口占用已由预探测提前拦下），保留它是防御性的：万一将来
    SDK 改用普通异常，这里也能给出可读说明。
    """
    if error is None:
        return "服务未能启动。"
    cause = error.__cause__ or error.__context__
    if isinstance(error, SystemExit) and cause is not None:
        return f"{type(cause).__name__}: {cause}"
    if isinstance(error, SystemExit):
        return f"服务未能启动（退出码 {error.code}）。"
    return f"{type(error).__name__}: {error}"


def _probe_port_available(host: str, port: int) -> bool:
    """探测端口能否绑定。

    端口被占用是常见的启动失败。若不预先探测，Uvicorn 会在绑定失败
    时抛 ``SystemExit(3)``——那是 ``BaseException`` 的子类，asyncio
    会把它当进程级信号直接终止事件循环，进程随即带着一段 traceback
    退出，用户看到的是 Python 栈而不是「端口被占用」。

    探测只是提前给出提示，真正的绑定仍由 Uvicorn 完成（探测与绑定
    之间存在极小的竞态窗口，绑定真失败时仍会走 SystemExit 路径，
    那种情况极罕见）。

    监听地址可能是 IPv6 字面量（配置里写成 ``[::1]``，带方括号），
    此时必须用 ``AF_INET6`` 探测，否则会因为地址族不匹配而误判成
    「端口被占用」。地址族判断不出来时一律按可用处理：宁可漏掉
    一次提示，也不要把正常启动拦下来。

    通配绑定（``0.0.0.0`` / ``::``）探测不出「端口已被具体地址占用」
    ——Windows 允许通配地址与具体地址共存，绑定照样成功。实测
    Uvicorn 在这种情况下确实能正常起来，因此这里的漏报是无害的：
    那个端口本来就可用。
    """
    if port == 0:
        # 0 表示由系统分配端口，此时无从探测。
        return True
    candidate = host.strip().strip("[]")
    family = socket.AF_INET
    if ":" in candidate:
        try:
            socket.inet_pton(socket.AF_INET6, candidate)
        except OSError:
            # 认不出地址族：可能是主机名，交由 Uvicorn 去解析，
            # 这里不拦。
            return True
        family = socket.AF_INET6
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((candidate, port))
    except OSError:
        return False
    return True


async def _shutdown(uvicorn_server: Any, task: asyncio.Task[None]) -> None:
    """让已监听的 Uvicorn 服务收尾，释放端口。

    收尾失败或超时都不能盖掉真正的错误原因，因此这里只记日志：
    端口会随进程退出一起释放。
    """
    uvicorn_server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.TimeoutError):  # pragma: no cover - 极端情况下的兜底
        LOGGER.error("bridge shutdown timed out")
    except Exception as exc:  # pragma: no cover - 收尾期异常不影响退出码
        LOGGER.error("bridge shutdown failed error_type=%s", type(exc).__name__)


async def _serve(config: BridgeConfig, log_level: str) -> int:
    """启动桥接服务并阻塞运行；返回进程退出码。

    发现文件写不出去时返回 2（配置错误），而不是抛异常让进程带着
    一段 traceback 退出：端口已经监起来了，客户端却拿不到令牌，
    这种半启动状态对使用者毫无意义。
    """
    import uvicorn

    core = build_core(config.core)
    # 端口为 0 时由系统分配实际端口；能力接口要用真实端口，
    # 否则客户端拿到的端点是 :0，连不上。
    bound_port: dict[str, int] = {}

    def current_port() -> int | None:
        return bound_port.get("port")

    app = build_bridge_app(core, config=config, port_provider=current_port)
    # 端口被占用是最常见的启动失败。先探测一次：绑不上就干净退出，
    # 不让 Uvicorn 去撞那个会终止事件循环的 SystemExit。
    if not _probe_port_available(config.host, config.port):
        print(
            f"桥接配置错误：无法在 {config.host}:{config.port} 上启动服务："
            f"端口已被占用（每个套接字地址只允许使用一次）。",
            file=sys.stderr,
            flush=True,
        )
        await core.aclose()
        return 2
    uvicorn_config = uvicorn.Config(
        app, host=config.host, port=config.port, log_level=log_level.lower(), access_log=False,
        ssl_certfile=config.tls_certfile, ssl_keyfile=config.tls_keyfile,
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)
    task = asyncio.create_task(uvicorn_server.serve())
    try:
        while not uvicorn_server.started:
            if task.done():
                # 服务启动阶段就失败了（地址不可绑定等）。
                # 这里不能用 task.result()：那个调用会把异常原样重抛，
                # 进程就带着一段 traceback 崩掉，而不是给出一句能看懂的
                # 中文说明。启动失败属于配置错误，按退出码 2 干净退出。
                #
                # 注意：端口被占用这条路走不到这里——Uvicorn 绑定失败时
                # 抛的是 SystemExit，它是 BaseException 的子类，asyncio
                # 会直接终止事件循环，连这行都执行不到。那条路已由上面
                # 的端口预探测拦下（提前以退出码 2 干净退出）。
                # 能走到这里的，是普通异常，task.exception() 取得到原因。
                error = task.exception()
                message = _startup_failure_message(error)
                print(
                    f"桥接配置错误：无法在 {config.host}:{config.port} 上启动服务：{message}",
                    file=sys.stderr,
                    flush=True,
                )
                return 2
            await asyncio.sleep(0.05)
        port = _bound_port(uvicorn_server, config.port)
        bound_port["port"] = port
        descriptor = {
            "schema_version": "1.0",
            "name": "davinci-gw-bridge",
            "version": BRIDGE_VERSION,
            "pid": os.getpid(),
            "host": config.host,
            "port": port,
            "path": config.path,
            "url": config.url_for(port),
            "token": config.effective_token,
            "core": config.core.describe(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if config.descriptor_path is not None:
            try:
                _write_descriptor(config.descriptor_path, descriptor)
            except (OSError, BridgeConfigError) as exc:
                # 端口已经监起来了，发现文件却写不出去：客户端拿不到令牌，
                # 留着服务只会让人以为能用。明确报错并退出。
                # _write_descriptor 的失败路径抛的是 BridgeConfigError
                # （ValueError 的子类），不是 OSError——只捕 OSError 会漏掉
                # 它，进程会带着一段 traceback 退出，而不是干净地报配置错误。
                uvicorn_server.should_exit = True
                print(
                    f"桥接配置错误：无法写入客户端发现文件 {config.descriptor_path}：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
                await _shutdown(uvicorn_server, task)
                return 2
        print(f"桥接服务已就绪：{descriptor['url']}", flush=True)
        if config.descriptor_path is not None:
            print(f"客户端发现文件：{config.descriptor_path}", flush=True)
        if not config.tls_enabled and not is_loopback_host(config.host):
            print(
                "警告：当前对外监听未启用 TLS，令牌与路由结果以明文传输；"
                "请确保该端口已置于受信加密隧道之后。",
                flush=True,
            )
        if config.wildcard_bind and not config.public_host:
            print(
                "警告：已绑定到全部接口但未指定 --public-host，"
                f"对外通告的地址为 {config.advertised_host}，远端客户端无法连接；"
                "请用 --public-host 指定客户端可达的主机名或 IP。",
                flush=True,
            )
        try:
            await task
        except Exception as exc:  # noqa: BLE001 - 运行期故障转成可读说明
            # 服务在运行期间崩溃：同样不该以裸 traceback 结束。
            # 这不是配置错误（配置已经被接受过了），用退出码 1 表示
            # 运行期失败。
            print(
                f"桥接服务异常退出：{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        return 0
    finally:
        await core.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s|%(levelname)s|%(name)s|%(message)s",
    )
    try:
        core_env = _parse_env(args.core_env)
    except BridgeConfigError as exc:
        print(f"桥接配置错误：{exc}", file=sys.stderr)
        return 2
    core_config = CoreConfig(
        mode=args.core,
        target=args.core_target,
        args=tuple(args.core_arg),
        env=core_env,
        token=args.core_token,
        timeout_seconds=args.core_timeout,
    )
    if args.core_command:
        try:
            command, command_args = _split_command(args.core_command)
        except BridgeConfigError as exc:
            print(f"桥接配置错误：{exc}", file=sys.stderr)
            return 2
        core_config = CoreConfig(
            mode=args.core,
            target=core_config.target or command,
            args=tuple(command_args) + core_config.args,
            env=core_config.env,
            token=core_config.token,
            timeout_seconds=core_config.timeout_seconds,
        )
    descriptor_path = None if args.no_descriptor else (
        Path(args.descriptor) if args.descriptor else _default_descriptor_path()
    )
    token_error = _discovery_token_error(args.no_descriptor, args.token)
    if token_error is not None:
        print(f"桥接配置错误：{token_error}", file=sys.stderr)
        return 2
    config = BridgeConfig(
        host=args.host,
        port=args.port,
        path=args.path,
        token=args.token,
        core=core_config,
        descriptor_path=descriptor_path,
        allow_remote=args.allow_remote,
        tls_certfile=args.tls_certfile,
        tls_keyfile=args.tls_keyfile,
        allow_remote_insecure=args.allow_remote_insecure,
        allowed_hosts=tuple(args.allowed_host),
        public_host=args.public_host,
    )
    try:
        config.validate()
    except BridgeConfigError as exc:
        print(f"桥接配置错误：{exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_serve(config, args.log_level))
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
