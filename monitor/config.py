"""Runtime configuration contains only public host metadata and per-node credentials.

The deployed server needs Python 3.9+ / SQLite 3.25+, not Ansible or SSH keys.
"""

import json
from pathlib import Path

from .models import Host
from .ui import parse_ui


def valid_token(value):
    return (
        isinstance(value, str)
        and 16 <= len(value) <= 256
        and value.isascii()
        and value.isprintable()
        and not any(c.isspace() for c in value)
    )


def load_config(path):
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    return parse_config(config)


def parse_config(config):
    """Validate the public version=1 configuration without filesystem side effects."""
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("监控配置必须是 version=1 的 JSON 对象")
    parse_ui(config.get("ui", {}))
    interval = config.get("interval", 30)
    stale = config.get("stale_after", 100)
    retention = config.get("retention_days", 30)
    if type(interval) is not int or not 5 <= interval <= 3600:
        raise ValueError("配置 interval 应为 5—3600 秒")
    if type(stale) is not int or not max(30, interval * 2) <= stale <= 86400:
        raise ValueError("stale_after 应不少于 30 秒且不少于两倍采样间隔")
    if type(retention) is not int or not 1 <= retention <= 365:
        raise ValueError("retention_days 应为 1—365")
    if not valid_token(config.get("viewer_token")):
        raise ValueError("viewer_token 必须为至少 16 位无空白的可打印 ASCII 字符")
    entries = config.get("hosts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("配置 hosts 必须是非空列表")
    hosts, credentials = [], {}
    used_tokens = {config["viewer_token"]}
    for entry in entries:
        if not isinstance(entry, dict) or not entry:
            raise ValueError("无效的 hosts 条目")
        name, address = entry.get("name"), entry.get("address")
        groups, token = entry.get("groups", []), entry.get("agent_token")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 128
            or name in credentials
        ):
            raise ValueError("节点名称无效或重复")
        if not isinstance(address, str) or not address or len(address) > 255:
            raise ValueError("节点地址无效")
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise ValueError("节点 groups 应为字符串列表")
        if not valid_token(token) or token in used_tokens:
            raise ValueError("每个节点必须使用独立于其他节点及 viewer_token 的有效令牌")
        used_tokens.add(token)
        credentials[name] = token
        hosts.append(Host(name, address, groups=tuple(groups)))
    return config, hosts, credentials
