"""Load center configuration without creating state, databases, or listeners."""

import json
from pathlib import Path

from .models import Host
from .schemas.common import valid_token, validate
from .schemas.config import CONFIG_VERSION, ServerConfig

__all__ = [
    "CONFIG_VERSION",
    "ServerConfig",
    "load_config",
    "parse_config",
    "valid_token",
]


def load_config(path):
    with Path(path).open(encoding="utf-8") as stream:
        return parse_config(json.load(stream))


def parse_config(config):
    model = validate(ServerConfig, config)
    hosts = [Host(h.name, h.address, groups=tuple(h.groups)) for h in model.hosts]
    credentials = {h.name: h.agent_token for h in model.hosts}
    # Preserve optional/unknown fields and omission in generated configurations.
    return config, hosts, credentials
