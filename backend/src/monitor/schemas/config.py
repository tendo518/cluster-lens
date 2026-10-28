"""Authoritative version 1 center configuration models, shared with build tools."""

from typing import Annotated

from pydantic import Field, field_validator, model_validator

from .common import Token, WireModel, described, integer, invalid, version
from .ui import UIConfig

CONFIG_VERSION = 1
VERSION_ERROR = "监控配置必须是 version=1 的 JSON 对象"
TOKEN_ERROR = "每个节点必须使用独立于其他节点及 viewer_token 的有效令牌"


class ConfigHost(WireModel):
    name: described(
        Annotated[str, Field(min_length=1, max_length=128)], "节点名称无效或重复"
    )
    address: described(
        Annotated[str, Field(min_length=1, max_length=255)], "节点地址无效"
    )
    groups: described(list[str], "节点 groups 应为字符串列表") = Field(
        default_factory=list
    )
    # Wire key remains stable; its schema title names the component clearly.
    agent_token: described(Token, TOKEN_ERROR) = Field(
        repr=False, title="Node Reporter Token"
    )

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict) or not value:
            invalid("无效的 hosts 条目")
        return value


class ServerConfig(WireModel):
    version: version(VERSION_ERROR)
    ui: UIConfig = Field(default_factory=UIConfig)
    interval: integer("配置 interval 应为 5—3600 秒", 5, 3600) = 30
    stale_after: integer(
        "stale_after 应不少于 30 秒且不少于两倍采样间隔", 30, 86400
    ) = Field(default=100, validate_default=True)
    retention_days: integer("retention_days 应为 1—365", 1, 365) = 30
    viewer_token: described(
        Token, "viewer_token 必须为至少 16 位无空白的可打印 ASCII 字符"
    ) = Field(repr=False)
    hosts: described(
        Annotated[list[ConfigHost], Field(min_length=1)], "配置 hosts 必须是非空列表"
    )

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict):
            invalid(VERSION_ERROR)
        return value

    @field_validator("stale_after")
    @classmethod
    def sampling_window(cls, value, info):
        if value < info.data.get("interval", 30) * 2:
            invalid("stale_after 应不少于 30 秒且不少于两倍采样间隔")
        return value

    @field_validator("hosts")
    @classmethod
    def distinct_identities(cls, value, info):
        names = set()
        tokens = {info.data.get("viewer_token")}
        for host in value:
            if host.name in names:
                invalid("节点名称无效或重复")
            if host.agent_token in tokens:
                invalid(TOKEN_ERROR)
            names.add(host.name)
            tokens.add(host.agent_token)
        return value
