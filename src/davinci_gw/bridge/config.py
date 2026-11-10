"""桥接服务的监听参数与独立核心配置。"""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from davinci_gw.security.redaction import (
    REDACTED,
    SENSITIVE_NAMES,
    _normalize_name,
    is_sensitive_name,
    looks_like_credential,
    redact_value,
)

CoreMode = Literal["embedded", "stdio", "http"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PATH = "/mcp"
LOOPBACK_NAMES = frozenset({"localhost"})

# 绑定到全部接口的主机名；此时无法枚举客户端实际使用的主机名或 IP。
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})

class BridgeConfigError(ValueError):
    """桥接配置不合法；调用方必须拒绝启动。"""


def is_loopback_host(host: str) -> bool:
    """判断监听地址是否只能在本机到达。"""
    candidate = host.strip().strip("[]").lower()
    if candidate in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def is_ipv6_literal(host: str) -> bool:
    """判断是否为 IPv6 字面量。"""
    candidate = host.strip().strip("[]")
    if ":" not in candidate:
        return False
    try:
        return isinstance(ipaddress.ip_address(candidate), ipaddress.IPv6Address)
    except ValueError:
        return False


def format_host(host: str) -> str:
    """把主机名格式化为可用于 URL 与 Host 头的形式。

    IPv6 字面量必须带方括号：URL 里是 ``[::1]``，HTTP ``Host`` 头也是
    ``[::1]:8765``。少了方括号既拼不出合法 URL，也匹配不上白名单。
    """
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        return candidate
    if is_ipv6_literal(candidate):
        return f"[{candidate}]"
    return candidate


def redact_url(target: str) -> str:
    """抹掉 URL 中的用户凭据与敏感查询参数。

    核心目标可能写成 ``https://user:pass@host/mcp?token=xxx``，
    这类值一旦原样进入对外描述，持有 Bridge 令牌的客户端就能拿到
    超出 Bridge 自身权限的凭据。

    URL 解析不了时不能把原文交出去：畸形地址（例如方括号不配对的
    IPv6）同样可能带着账号密码，解析失败就一律按整体脱敏处理。
    """
    try:
        parts = urlsplit(target)
    except ValueError:
        # 解析失败：宁可整个目标都抹掉，也不冒泄出密码或令牌的风险。
        return REDACTED
    if not parts.scheme:
        # 没有协议头就不是 URL（本地路径、可执行文件路径），按原样保留。
        return target
    if not parts.netloc:
        # 有协议头却没有主机部分：``http:///mcp?token=secret`` 这类写法
        # 解析得出来，但查询参数里的令牌仍是凭据，不能原样外泄。
        if parts.query:
            return urlunsplit((parts.scheme, "", parts.path, REDACTED, parts.fragment))
        return target
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, _, hostport = netloc.rpartition("@")
        name, separator, _ = userinfo.partition(":")
        netloc = f"{name}:{REDACTED}@{hostport}" if separator else f"{REDACTED}@{hostport}"
    query = parts.query
    if query:
        pairs = [
            # 键名敏感就整体替换；键名普通时，取值内部仍可能藏着凭据
            # （upstream=http://user:pass@host、state=sk-live-…），
            # 所以取值也要过一遍值级脱敏。
            (key, REDACTED if is_sensitive_name(key) else redact_value(value))
            for key, value in parse_qsl(query, keep_blank_values=True)
        ]
        query = urlencode(pairs)
    fragment = parts.fragment
    if fragment:
        # fragment 里同样可能藏令牌（OAuth 风格的 #access_token=…）。
        fragment = redact_value(fragment)
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def redact_args(args: tuple[str, ...]) -> list[str]:
    """抹掉命令行参数中的凭据。

    参数有三种形态，都要处理：

    - ``--token=xxx``：参数名敏感，整个取值替换为 ``***``；
    - ``--token xxx``：参数名敏感，下一个参数整体替换为 ``***``；
    - 取值本身含凭据（``--header "Authorization: Bearer xxx"``、
      ``--config={"api_token":"xxx"}``）：由 ``redact_value`` 处理。

    这里要特别注意：不能把"整串参数"直接当参数名去判敏感。
    ``--config={"api_token":"abc123token"}`` 整串规范化后以 token 结尾，
    若按整串判定就会走进"参数名敏感"分支，反而跳过了取值脱敏，
    凭据就原样进了对外描述。
    """
    redacted: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            redacted.append(REDACTED)
            redact_next = False
            continue
        name, separator, _ = arg.partition("=")
        if separator:
            # --name=value 形态：只按参数名判断敏感与否。
            if is_sensitive_name(name):
                redacted.append(f"{name}={REDACTED}")
            else:
                redacted.append(redact_value(arg))
            continue
        if arg.startswith("-") and is_sensitive_name(arg):
            # 开关形态（--token）：值在下一个参数里。
            redacted.append(redact_value(arg))
            redact_next = True
            continue
        # 裸取值：整体就是一个凭据串时直接抹掉。
        if looks_like_credential(arg):
            redacted.append(REDACTED)
            continue
        redacted.append(redact_value(arg))
    return redacted


# 藏在参数值内部的凭据写法，例如 "Authorization: Bearer xxx"、
# '--config={"api_token":"xxx"}'、"token=xxx"。键名与取值之间允许引号，
# 以便识别 api_token、x-api-key 这类变体；取值优先按引号里的整段匹配，


@dataclass(frozen=True)
class CoreConfig:
    """独立核心的配置。

    核心与桥接进程解耦：桥接只负责转发，核心可以是同进程实现、
    独立的 STDIO 子进程，或内网另一台机器上的 HTTP 服务。
    """

    mode: CoreMode = "embedded"
    target: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    token: str | None = None
    timeout_seconds: float = 3600.0

    def validate(self) -> None:
        if self.mode not in ("embedded", "stdio", "http"):
            raise BridgeConfigError(f"未知的核心模式：{self.mode}")
        if self.mode in ("stdio", "http") and not self.target:
            raise BridgeConfigError(f"核心模式 {self.mode} 必须提供核心目标。")
        if self.mode == "http" and not str(self.target).lower().startswith(("http://", "https://")):
            raise BridgeConfigError("http 核心目标必须是 http 或 https 地址。")
        if self.mode == "http":
            # 光有协议头不够：http:///mcp 这种写法没有主机部分，
            # 既连不上，查询参数里的令牌也会跟着外泄。
            try:
                parts = urlsplit(str(self.target))
            except ValueError as exc:
                raise BridgeConfigError(f"http 核心目标不是合法地址：{exc}") from exc
            if not parts.netloc:
                raise BridgeConfigError("http 核心目标必须包含主机名或 IP。")
        if self.timeout_seconds <= 0:
            raise BridgeConfigError("核心超时必须为正数。")

    def describe(self) -> dict[str, object]:
        """给出可公开的核心描述；令牌与目标中的凭据一律脱敏。

        该描述会随 ``get_gateway_capabilities`` 返回给任意通过鉴权的
        客户端，也会写进客户端发现文件，因此不能包含任何可能越权的
        凭据：令牌字段本身不参与描述，目标地址与参数中的密码、
        密钥类取值会被替换为 ``***``。
        """
        return {
            "mode": self.mode,
            "target": redact_url(self.target) if self.target else self.target,
            "args": redact_args(self.args),
        }


@dataclass(frozen=True)
class BridgeConfig:
    """桥接服务的监听、鉴权与核心配置。"""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    path: str = DEFAULT_PATH
    token: str | None = None
    core: CoreConfig = field(default_factory=CoreConfig)
    descriptor_path: Path | None = None
    allow_remote: bool = False
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    allow_remote_insecure: bool = False
    allowed_hosts: tuple[str, ...] = ()
    public_host: str | None = None

    def __post_init__(self) -> None:
        """在构造时确定访问令牌。

        令牌必须在整个进程生命周期内保持不变：客户端发现文件里的令牌
        与服务端鉴权用的令牌必须逐字一致，否则调用会被判为 401。
        因此这里一次性生成并固定，而不是每次读取时重新生成。
        """
        if not self.token:
            object.__setattr__(self, "token", secrets.token_urlsafe(32))

    @property
    def tls_enabled(self) -> bool:
        """是否以 TLS 提供服务。"""
        return bool(self.tls_certfile and self.tls_keyfile)

    @property
    def wildcard_bind(self) -> bool:
        """是否绑定到全部网络接口。"""
        return self.host.strip().strip("[]") in WILDCARD_HOSTS

    @property
    def advertised_host(self) -> str:
        """对外通告的客户端可达主机名。

        绑定全部接口时监听地址本身（``0.0.0.0``）不是一个可连接的地址，
        把它写进发现文件与能力接口，客户端会连到"未指定地址"上去。
        这时优先用运维显式登记的 ``public_host``；没有登记则退回回环
        地址，并在启动日志中提示远端客户端不可达。
        """
        if self.public_host:
            return self.public_host
        if self.wildcard_bind:
            return DEFAULT_HOST
        return self.host

    def validate(self) -> None:
        if not self.path.startswith("/"):
            raise BridgeConfigError("服务路径必须以 / 开头。")
        if not 0 <= self.port <= 65535:
            raise BridgeConfigError("端口必须在 0 到 65535 之间。")
        token = self.effective_token
        if not token.isascii():
            # Authorization 头按字节比较，非 ASCII 令牌无法可靠送达；
            # 与其等到客户端每次调用都 401，不如启动时就说清楚。
            raise BridgeConfigError("访问令牌只能使用 ASCII 字符。")
        if not token.strip():
            raise BridgeConfigError("访问令牌不能为空。")
        if not self.allow_remote and not is_loopback_host(self.host):
            raise BridgeConfigError("默认只允许回环监听；确需对外监听时必须显式开启允许远端。")
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise BridgeConfigError("TLS 证书与私钥必须同时提供。")
        if not is_loopback_host(self.host) and not self.tls_enabled and not self.allow_remote_insecure:
            # 对外监听时令牌与路由结果会跨主机传输，明文等同于把凭据
            # 交给链路上的旁观者。要么启用 TLS，要么由运维显式声明
            # 该端口已置于受信加密隧道之后。
            raise BridgeConfigError(
                "对外监听必须启用 TLS：提供 --tls-certfile 与 --tls-keyfile；"
                "若端口已置于受信加密隧道之后，请显式使用 --allow-remote-insecure。"
            )
        self.core.validate()

    @property
    def effective_token(self) -> str:
        """返回本次启动确定的访问令牌；多次读取结果一致。"""
        assert self.token is not None  # noqa: S101 - 由 __post_init__ 保证
        return self.token

    def url_for(self, port: int | None = None) -> str:
        """返回客户端可用的服务地址。"""
        host = format_host(self.advertised_host)
        scheme = "https" if self.tls_enabled else "http"
        return f"{scheme}://{host}:{port if port is not None else self.port}{self.path}"
