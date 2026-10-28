"""Generate application config from public node JSON, independently of any inventory."""

import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from monitor.config import CONFIG_VERSION, load_config, parse_config
from monitor.ui import parse_ui

from .files import private_write


def generate(
    directory, nodes, url, interval=30, stale_after=100, retention_days=30, ui=None
):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/api/agent/report"
    ):
        raise ValueError(
            "上报地址必须是无凭据、无查询参数的 http(s)://主机[:端口]/api/agent/report"
        )
    # Validate the port too, even though it is optional in HTTP(S) URLs.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("上报地址端口应为 1—65535")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("节点清单必须是非空 JSON 数组")
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {"name", "address", "groups"}:
            raise ValueError(
                "节点清单仅允许 name、address、groups，不接收 SSH 配置或令牌"
            )
        name = node.get("name")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
            or name in (".", "..")
        ):
            raise ValueError("配置文件节点名只能包含字母、数字、点、横线和下划线")
    directory = Path(directory)
    previous = directory / "server.json"
    old = load_config(previous)[0] if previous.exists() else {}
    old_tokens = {h["name"]: h["agent_token"] for h in old.get("hosts", [])}
    config = {
        "version": CONFIG_VERSION,
        "interval": interval,
        "stale_after": stale_after,
        "retention_days": retention_days,
        "viewer_token": old.get("viewer_token") or secrets.token_urlsafe(32),
        "hosts": [
            {
                "name": node["name"],
                "address": node.get("address"),
                "groups": node.get("groups", []),
                "agent_token": old_tokens.get(node["name"])
                or secrets.token_urlsafe(32),
            }
            for node in nodes
        ],
    }
    if ui is not None:
        config["ui"] = parse_ui(ui)
    elif "ui" in old:
        config["ui"] = old["ui"]
    parse_config(config)  # Fail before replacing any existing configuration.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    agents = directory / "agents"
    agents.mkdir(mode=0o700, exist_ok=True)
    agents.chmod(0o700)
    private_write(previous, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    for host in config["hosts"]:
        agent = {
            "version": 1,
            "host": host["name"],
            "url": url,
            "token": host["agent_token"],
            "interval": interval,
            "timeout": 8,
        }
        private_write(
            agents / (host["name"] + ".json"),
            json.dumps(agent, ensure_ascii=True, indent=2) + "\n",
        )
    names = {node["name"] for node in nodes}
    for path in agents.glob("*.json"):
        if path.stem not in names:
            path.unlink()
    private_write(directory / "viewer-token", config["viewer_token"] + "\n")
    return config
