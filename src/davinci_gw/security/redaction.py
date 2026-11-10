"""凭据脱敏原语。

放在中立层，是为了让桥接层（``davinci_gw.bridge``）与核心层
（``davinci_gw.application``）共用同一套判定，避免同一段凭据
在不同出口得到不同的处理。

判定规则刻意保守：只抹掉形态明确的凭据，普通业务值
（报文名、CAN ID、对象路径）不受影响。
"""

from __future__ import annotations

import re

REDACTED = "***"

# 规范化后与之相等的名称视为敏感项：其取值不进入任何对外描述。
SENSITIVE_NAMES = frozenset({
    "token", "accesstoken", "authtoken", "refreshtoken", "idtoken", "bearertoken",
    "secret", "clientsecret", "apisecret", "sharedsecret",
    "password", "passwd", "pwd", "passphrase", "pass",
    "credential", "credentials", "apikey", "accesskey", "secretkey", "privatekey",
    "sessionkey", "auth", "authorization", "key",
    # 会话 Cookie 本身就是可直接复用的凭据：--header "Cookie: session=…"
    # 这种写法原先原样进入对外描述。
    "cookie", "cookies", "setcookie", "session", "sessionid", "sessioncookie", "csrftoken",
})

# 以这些词结尾的名称同样是敏感项：--api-token、--user-token 这类变体
# 规范化后不等于集合里的名字，但仍然是令牌类参数。
SENSITIVE_SUFFIXES = ("token", "secret", "password", "passwd", "credential")

# 复合写法里可单独成段的强凭据词：--x-api-key、--my-secret 这类。
# 这些词本身几乎只出现在凭据语境里，命中即可判定敏感。
_STRONG_SEGMENTS = frozenset({
    "token", "secret", "password", "passwd", "passphrase",
    "credential", "credentials", "apikey", "accesskey", "secretkey", "privatekey",
    "sessionkey", "authorization", "csrftoken",
})

# 弱凭据词：单独出现时是凭据（Cookie 头、session 令牌、key），但和其它
# 词组合多半是普通字段名（cookie_recipe、session_count、key_recipe），
# 组合时要放过。
_SOFT_SEGMENT_NAMES = frozenset({"cookie", "cookies", "session", "auth", "pass", "key"})

# 免得把同一段里的无关字段一起抹掉。
# 键名两侧可能带转义引号（命令行里常见 --config="{\"api_token\":\"xxx\"}"），
# 那种形态同样要能识别。
_KEY_VALUE_PATTERN = re.compile(r"(?P<key>[\w.\-]+)(?:\\?[\"'])?\s*[:=]\s*")

# URL 里内嵌的账号密码：https://user:pass@host
# 密码本身可能含冒号（https://u:p:a@h），所以密码段要一路吃到 @ 之前。
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^\s/?#:@]+):(?P<password>[^\s/?#@]+)@"
)

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+(\"[^\"]*\"|'[^']*'|\S+)")

# 常见的令牌前缀：sk-live-、sk-、ghp_、gho_、xoxb-、AKIA 等。
# 这些形态的取值本身就是凭据，参数名未必带敏感字样。
CREDENTIAL_PREFIXES = (
    "sk-", "sk_live_", "sk_test_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
    "xoxb-", "xoxp-", "xoxa-", "akia", "aiza", "ya29.", "eyj",
)


def _normalize_name(name: str) -> str:
    """把名称规范化为只含小写字母数字的形式，便于精确匹配。"""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def is_sensitive_name(name: str) -> bool:
    """判断名称是否属于可能承载凭据的名称。"""
    normalized = _normalize_name(name.lstrip("-"))
    if normalized in SENSITIVE_NAMES:
        return True
    if normalized.endswith(SENSITIVE_SUFFIXES):
        return True
    # 名称由若干段组成（以 _ - . 分隔）时，其中一段是区分度高的凭据词，
    # 整体即视为敏感：--x-api-key 这类写法要能识别出来。
    # 按段判断而不是按子串，"monkey"、"bypass" 这类普通词不会被误判。
    segments = [
        _normalize_name(segment)
        for segment in re.split(r"[_\-.]", name.lstrip("-"))
        if segment
    ]
    # 命中强凭据词（token、secret、credential…）直接算敏感。
    if any(segment in _STRONG_SEGMENTS for segment in segments):
        return True
    # "api-key"、"access-key" 这类复合写法：api/access 与 key 相邻时
    # 整体就是凭据名，但要排除 api_url、api_version 这类普通组合。
    for index, segment in enumerate(segments[:-1]):
        if segment in ("api", "access", "secret", "private", "session") and (
            segments[index + 1] in ("key", "secret")
        ):
            return True
    # 命中弱凭据词（cookie、session、auth、pass、key）时，只有名字整体
    # 就是这个词才算敏感：cookie_recipe、session_count、key_recipe 这类
    # 普通字段名不能被当成凭据。
    return len(segments) == 1 and segments[0] in _SOFT_SEGMENT_NAMES


def _redacted_placeholder(raw: str) -> str:
    """保留原有引号形态，把取值换成占位符。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return f"{raw[0]}{REDACTED}{raw[0]}"
    # 转义引号形态：\"abc\" -> \"***\"
    if len(raw) >= 4 and raw.startswith("\\") and raw[1] in "\"'" and raw.endswith(raw[1]):
        return f"\\{raw[1]}{REDACTED}\\{raw[1]}"
    return REDACTED


def _value_span(text: str, start: int) -> tuple[int, int]:
    """给出取值在文本中的区间。

    带引号的取值取到收尾引号为止；转义引号（``\\"…\\"``）取到收尾的
    转义引号为止；不带引号的取值取到分隔符（逗号、分号、右括号、
    换行或引号）之前。
    """
    if start >= len(text):
        return start, start
    # 转义引号：\"abc\" 形式的取值。
    if text.startswith("\\\"", start) or text.startswith("\\'", start):
        quote = text[start + 1]
        closing = f"\\{quote}"
        end = text.find(closing, start + 2)
        return (start, len(text)) if end == -1 else (start, end + 2)
    quote = text[start]
    if quote in "\"'":
        end = text.find(quote, start + 1)
        return (start, len(text)) if end == -1 else (start, end + 1)
    index = start
    while index < len(text) and text[index] not in ",;}]\r\n\"'":
        index += 1
    return start, index


def looks_like_credential(text: str) -> bool:
    """判断一个裸取值是否整个就是凭据。

    两种情况算凭据：

    - 整体像敏感名，但不是敏感词典里的原词（``my-token-value``）；
      敏感词典里的单个普通词（``session``、``token``）保留原样。
    - 带常见令牌前缀（``sk-live-``、``ghp_``、``xoxb-`` 这类），
      参数名未必带敏感字样，但取值本身就是凭据。

    含路径分隔符的取值一律不算凭据：那是文件位置。
    """
    if any(separator in text for separator in ("/", "\\", ":", ";")):
        return False
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in CREDENTIAL_PREFIXES):
        return True
    normalized = _normalize_name(text.lstrip("-"))
    if normalized in SENSITIVE_NAMES:
        return False
    return is_sensitive_name(text)


def redact_value(text: str) -> str:
    """抹掉文本中以凭据语法出现的片段。

    凭据可能藏在三种位置：键值对（``api_token=xxx``、``"api_token":"xxx"``）、
    URL 的用户信息段（``https://user:pass@host``）、以及裸的
    ``Bearer xxx`` 头。三种都要抹掉，否则持有凭据的调用方
    能从能力接口、发现文件或问题表里拿到别人的凭据。

    判定只依据形态：键名命中敏感名集合、或值本身是 ``Bearer``/``Basic``
    头、或 URL 带用户信息段。报文名、CAN ID、对象路径这类业务值不受影响。
    """
    pieces: list[str] = []
    position = 0
    for match in _KEY_VALUE_PATTERN.finditer(text):
        if match.start() < position:
            # 落在已经处理过的取值区间里，不再重复处理。
            continue
        if not is_sensitive_name(match.group("key")):
            continue
        value_start, value_end = _value_span(text, match.end())
        if value_end <= value_start:
            continue
        pieces.append(text[position:match.start()])
        pieces.append(text[match.start():value_start])
        pieces.append(_redacted_placeholder(text[value_start:value_end]))
        position = value_end
    pieces.append(text[position:])
    redacted = "".join(pieces)
    redacted = _URL_USERINFO_PATTERN.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@",
        redacted,
    )
    redacted = _BEARER_PATTERN.sub(lambda match: f"{match.group(1)} {REDACTED}", redacted)
    # 整段就是一个凭据串时（sk-live-…、ghp_… 这类令牌前缀），
    # 前面的规则都不会命中，必须整体抹掉。
    if looks_like_credential(redacted.strip()):
        return REDACTED
    return redacted
