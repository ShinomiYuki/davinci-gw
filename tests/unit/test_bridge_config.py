"""桥接配置的校验规则。"""

from __future__ import annotations

import pytest

from davinci_gw.bridge.config import (
    BridgeConfig,
    BridgeConfigError,
    CoreConfig,
    is_loopback_host,
    is_sensitive_name,
    redact_url,
)


def test_redact_url_masks_credentials_hidden_inside_query_values() -> None:
    """查询参数的值内部也可能藏着凭据，不能只看键名。

    核心 URL 会原样进入能力接口与发现文件，值里的 user:pass 与令牌前缀
    都要抹掉，否则持有桥接令牌的客户端能拿到核心自己的凭据。
    """
    redacted = redact_url(
        "http://core/mcp?upstream=http%3A%2F%2Falice%3Ahunter2%40db%3A5432%2Fx"
    )
    assert "hunter2" not in redacted
    assert redact_url("http://core/mcp?state=sk-live-abcdef123").count("sk-live") == 0
    # 普通查询参数不受影响。
    assert "version" in redact_url("http://core/mcp?version=4.85")


def test_non_ascii_token_is_rejected_before_start() -> None:
    """令牌必须能在 HTTP 头里原样送达，非 ASCII 的要在启动时就说清楚。"""
    config = BridgeConfig(token="p\u00e4sswort")
    with pytest.raises(BridgeConfigError):
        config.validate()


def test_default_config_is_loopback_only() -> None:
    config = BridgeConfig()
    config.validate()
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.path == "/mcp"


def test_loopback_detection_covers_names_and_addresses() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")


def test_non_loopback_host_requires_explicit_opt_in() -> None:
    with pytest.raises(BridgeConfigError):
        BridgeConfig(host="0.0.0.0").validate()
    # 显式允许对外监听还不够：明文监听必须再显式声明或启用 TLS。
    with pytest.raises(BridgeConfigError):
        BridgeConfig(host="0.0.0.0", allow_remote=True).validate()
    BridgeConfig(host="0.0.0.0", allow_remote=True, allow_remote_insecure=True).validate()


def test_remote_listener_requires_tls_unless_tunnel_is_declared() -> None:
    """对外监听必须加密：令牌与路由结果不能明文跨主机传输。"""
    with pytest.raises(BridgeConfigError):
        BridgeConfig(host="192.168.1.10", allow_remote=True).validate()
    with pytest.raises(BridgeConfigError):
        BridgeConfig(host="192.168.1.10", allow_remote=True, tls_certfile="cert.pem").validate()
    BridgeConfig(
        host="192.168.1.10", allow_remote=True, tls_certfile="cert.pem", tls_keyfile="key.pem",
    ).validate()
    BridgeConfig(
        host="192.168.1.10", allow_remote=True, allow_remote_insecure=True,
    ).validate()


def test_remote_listener_advertises_https_when_tls_is_enabled() -> None:
    config = BridgeConfig(
        host="192.168.1.10", allow_remote=True, tls_certfile="cert.pem", tls_keyfile="key.pem",
    )
    assert config.tls_enabled
    assert config.url_for().startswith("https://192.168.1.10:8765/mcp")
    # 回环监听不受远端策略影响，仍是明文本地端口。
    assert BridgeConfig().url_for().startswith("http://")


def test_wildcard_bind_advertises_a_reachable_host() -> None:
    """绑定全部接口时不能把 0.0.0.0 写进发现文件，客户端连不上。"""
    wildcard = BridgeConfig(host="0.0.0.0", allow_remote=True, allow_remote_insecure=True)
    assert wildcard.advertised_host == "127.0.0.1"
    assert "0.0.0.0" not in wildcard.url_for()

    explicit = BridgeConfig(
        host="0.0.0.0", allow_remote=True, allow_remote_insecure=True,
        public_host="gateway.internal",
    )
    assert explicit.advertised_host == "gateway.internal"
    assert "gateway.internal" in explicit.url_for()


def test_ipv6_literals_are_bracketed_in_urls() -> None:
    """IPv6 字面量必须带方括号，否则拼不出合法 URL。"""
    config = BridgeConfig(host="2001:db8::1", allow_remote=True, allow_remote_insecure=True)
    assert config.url_for().startswith("http://[2001:db8::1]:8765/mcp")
    assert BridgeConfig(host="::1").url_for(9100) == "http://[::1]:9100/mcp"


def test_invalid_port_and_path_are_rejected() -> None:
    with pytest.raises(BridgeConfigError):
        BridgeConfig(port=70000).validate()
    with pytest.raises(BridgeConfigError):
        BridgeConfig(path="mcp").validate()


def test_core_modes_have_their_own_requirements() -> None:
    CoreConfig(mode="embedded").validate()
    with pytest.raises(BridgeConfigError):
        CoreConfig(mode="stdio").validate()
    with pytest.raises(BridgeConfigError):
        CoreConfig(mode="http", target="ftp://host").validate()
    with pytest.raises(BridgeConfigError):
        CoreConfig(mode="http", target="http://127.0.0.1:9100/mcp", timeout_seconds=0).validate()
    CoreConfig(mode="http", target="http://127.0.0.1:9100/mcp").validate()
    CoreConfig(mode="stdio", target="davinci-gw-mcp.exe").validate()


def test_effective_token_is_generated_when_missing() -> None:
    config = BridgeConfig()
    token = config.effective_token
    assert token and len(token) >= 32
    assert BridgeConfig(token="fixed-token").effective_token == "fixed-token"


def test_effective_token_stays_the_same_for_the_whole_process() -> None:
    """令牌在进程生命周期内固定：发现文件与服务端鉴权必须拿到同一个值。"""
    config = BridgeConfig()
    assert config.effective_token == config.effective_token
    assert config.token == config.effective_token
    # 不同实例使用不同令牌，避免多实例互相串用。
    assert BridgeConfig().effective_token != config.effective_token


def test_core_description_never_leaks_the_token() -> None:
    described = CoreConfig(mode="http", target="http://127.0.0.1:9100/mcp", token="secret").describe()
    assert "secret" not in str(described)
    assert described["mode"] == "http"


def test_core_description_redacts_credentials_in_the_target() -> None:
    """核心目标里的账号密码不能随能力接口泄露给 Bridge 客户端。"""
    described = CoreConfig(
        mode="http", target="https://alice:hunter2@core.internal/mcp?token=abc123&mode=fast",
    ).describe()
    text = str(described)
    assert "hunter2" not in text
    assert "abc123" not in text
    assert "alice" in text
    assert "core.internal" in text
    assert "mode=fast" in text


def test_core_description_redacts_credentials_in_arguments() -> None:
    """核心命令行参数里的令牌不能随能力接口泄露。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--token", "abc123", "--api-key=def456", "--mode", "fast"),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "def456" not in text
    assert "--mode" in text
    assert "fast" in text


def test_core_description_redacts_credentials_embedded_in_argument_values() -> None:
    """凭据藏在参数值里时同样要抹掉，例如 --header "Authorization: Bearer xxx"。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--header", "Authorization: Bearer abc123", "--mode", "fast"),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "fast" in text


def test_core_description_redacts_bearer_values_without_a_key() -> None:
    """只有 Bearer 形式时也要抹掉后面的凭据。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe", args=("--auth", "Bearer abc123"),
    ).describe()
    assert "abc123" not in str(described)


def test_core_description_redacts_token_name_variants() -> None:
    """--api-token 这类变体名也要能识别，并抹掉它后面跟的凭据。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--api-token", "secret123", "--user-secret", "secret456"),
    ).describe()
    text = str(described)
    assert "secret123" not in text
    assert "secret456" not in text


def test_core_description_redacts_structured_credentials_in_argument_values() -> None:
    """JSON 形式的凭据也要抹掉：--config={"api_token":"xxx"}。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"api_token":"abc123","mode":"fast"}',),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "api_token" in text


def test_core_description_redacts_url_credentials_in_argument_values() -> None:
    """参数值里的 URL 账号密码同样不能外泄。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--base-url", "https://alice:hunter2@core.internal/mcp"),
    ).describe()
    text = str(described)
    assert "hunter2" not in text
    assert "alice" in text
    assert "core.internal" in text


def test_core_description_redacts_url_credentials_in_structured_values() -> None:
    """JSON 里嵌的 URL 也要抹掉账号密码。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"url":"http://admin:hunter2@10.0.0.5:9100/mcp","mode":"fast"}',),
    ).describe()
    text = str(described)
    assert "hunter2" not in text
    assert "10.0.0.5" in text
    assert "mode" in text


def test_core_description_redacts_short_credential_keys() -> None:
    """key、pwd、auth 这类短名键同样是凭据，不能因为名字短就漏掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"key":"K1","pwd":"P1","auth":"A1","mode":"fast"}',),
    ).describe()
    text = str(described)
    assert "K1" not in text
    assert "P1" not in text
    assert "A1" not in text
    assert "mode" in text
    assert "fast" in text


def test_core_description_redacts_url_credentials_in_the_fragment() -> None:
    """URL 的 fragment 里也可能藏令牌（OAuth 风格）。"""
    described = CoreConfig(
        mode="http", target="http://core.internal/mcp#access_token=abc123",
    ).describe()
    assert "abc123" not in str(described)


def test_core_description_redacts_url_passwords_containing_colons() -> None:
    """密码本身带冒号时也要抹掉。"""
    described = CoreConfig(
        mode="http", target="https://alice:p:a:ss@core.internal/mcp",
    ).describe()
    text = str(described)
    assert "p:a:ss" not in text
    assert "alice" in text


def test_core_description_redacts_escaped_quoted_json() -> None:
    """命令行里常见转义引号的 JSON 写法也要脱敏。

    ``--core-arg='--config={\\"api_token\\":\\"xxx\\"}'`` 传进来就是这个形态，
    键名两侧各带一个反斜杠引号。
    """
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config="{\\"api_token\\":\\"abc123\\"}"',),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "api_token" in text


def test_core_description_redacts_nested_json_credentials() -> None:
    """嵌套在对象里的凭据同样要抹掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"outer":{"api_token":"abc123"}}',),
    ).describe()
    assert "abc123" not in str(described)


def test_core_description_keeps_values_with_commas_intact() -> None:
    """取值里带逗号时不能把后面的字段一起抹掉，也不能漏掉凭据。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"note":"a,b","token":"abc123"}',),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "a,b" in text


def test_core_description_redacts_json_credentials_ending_with_a_sensitive_word() -> None:
    """取值恰好以 token / secret 结尾时也必须脱敏。

    这里的坑很隐蔽：整串参数规范化后以 token 结尾，若按"整串是不是敏感
    参数名"来判断，就会走进参数名分支而跳过取值脱敏，凭据原样进入对外
    描述。取值以什么结尾都不该影响脱敏。
    """
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"api_token":"abc123token"}',),
    ).describe()
    assert "abc123token" not in str(described)


def test_core_description_redacts_json_credentials_ending_with_secret() -> None:
    """同理：取值以 secret 结尾也要脱敏。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"client_secret":"my-secret"}',),
    ).describe()
    assert "my-secret" not in str(described)


def test_core_description_redacts_a_bare_credential_value() -> None:
    """裸取值本身就是凭据时要抹掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("my-token-value", "sk-live-abcdef123"),
    ).describe()
    text = str(described)
    assert "my-token-value" not in text
    assert "sk-live-abcdef123" not in text


def test_core_description_keeps_paths_and_ordinary_words() -> None:
    """路径与普通词不是凭据，不能被抹掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("C:\\keys\\token.txt", "session", "fast"),
    ).describe()
    text = str(described)
    assert "token.txt" in text
    assert "session" in text
    assert "fast" in text


def test_core_description_keeps_the_next_argument_name() -> None:
    """取值恰好是敏感词时，不能把下一个参数名一起抹掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--label", "session", "--output", "x.arxml"),
    ).describe()
    text = str(described)
    assert "--output" in text
    assert "x.arxml" in text


def test_core_description_keeps_ordinary_values_intact() -> None:
    """脱敏不能误伤普通字段：只有凭据位置的取值会被抹掉。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=('--config={"mode":"fast","monkey":"zoo","bypass":"yes"}',),
    ).describe()
    text = str(described)
    assert "fast" in text
    assert "zoo" in text
    assert "yes" in text


def test_sensitive_name_detection_covers_compound_variants() -> None:
    """复合写法的参数名要能识别，同时不能误伤普通词。"""
    assert is_sensitive_name("--x-api-token")
    assert is_sensitive_name("--api-token")
    assert is_sensitive_name("--user-secret")
    assert is_sensitive_name("--x-api-key")
    assert is_sensitive_name("--access-key")
    assert not is_sensitive_name("--monkey")
    assert not is_sensitive_name("--bypass")
    assert not is_sensitive_name("--mode")


def test_sensitive_name_detection_keeps_ordinary_field_names() -> None:
    """日常词组成的普通字段名不能被当成凭据。"""
    for name in ("cookie_recipe", "session_count", "key_recipe", "api_url", "api_version"):
        assert not is_sensitive_name(name), name


def test_core_description_redacts_cookie_headers() -> None:
    """会话 Cookie 是可直接复用的凭据，不能随能力接口泄露。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--header", "Cookie: session=abc123"),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "Cookie" in text


def test_core_description_redacts_session_and_csrf_values() -> None:
    """session、sessionid、CSRF 令牌同样要脱敏。"""
    described = CoreConfig(
        mode="stdio", target="davinci-gw-mcp.exe",
        args=("--header", "session=abc123", "--header", "X-CSRF-Token: def456"),
    ).describe()
    text = str(described)
    assert "abc123" not in text
    assert "def456" not in text


def test_core_description_redacts_a_malformed_core_url() -> None:
    """URL 解析失败时不能把原文交出去：畸形地址同样可能带着密码。"""
    described = CoreConfig(
        mode="http", target="http://alice:hunter2@[::1/mcp",
    ).describe()
    text = str(described)
    assert "hunter2" not in text
    assert "alice" not in text


def test_core_description_redacts_an_http_target_without_a_host() -> None:
    """有协议头却没有主机部分时，查询参数里的令牌同样不能外泄。"""
    described = CoreConfig(
        mode="http", target="http:///mcp?token=secret123",
    ).describe()
    text = str(described)
    assert "secret123" not in text


def test_http_core_target_must_carry_a_host() -> None:
    """http 核心目标必须包含主机名或 IP：这种地址连不通。"""
    with pytest.raises(BridgeConfigError):
        CoreConfig(mode="http", target="http:///mcp?token=secret123").validate()
    # 正常地址不受影响。
    CoreConfig(mode="http", target="http://127.0.0.1:9100/mcp").validate()


def test_url_for_formats_ipv6_hosts() -> None:
    assert BridgeConfig(host="127.0.0.1", port=8765).url_for() == "http://127.0.0.1:8765/mcp"
    assert BridgeConfig(host="::1", port=9000).url_for(9100) == "http://[::1]:9100/mcp"
