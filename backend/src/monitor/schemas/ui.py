"""Public presentation schema. No paths, URLs, credentials, or arbitrary markup."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import WireModel, described, invalid

THEMES = ("system", "light", "dark")
PALETTES = ("green", "blue", "purple", "amber", "teal", "rose")


def label(key, limit, *, blank=False):
    message = f"ui.{key} 应为不含控制字符且长度不超过 {limit} 的文本"
    return described(
        Annotated[
            str, Field(strict=True, max_length=limit, pattern=r"^[^\x00-\x1f\x7f]*$")
        ],
        message,
    )


class UIConfig(WireModel):
    model_config = ConfigDict(extra="forbid")
    title: label("title", 120) = "ClusterLens · 服务器监控"
    name: label("name", 40) = "ClusterLens"
    subtitle: label("subtitle", 60) = "SERVER MONITORING"
    mark: label("mark", 2) = "C"
    avatar: label("avatar", 3) = "CL"
    workspace: label("workspace", 60) = "服务器集群"
    workspace_subtitle: label("workspace_subtitle", 80) = "Monitoring workspace"
    heading: label("heading", 80) = "集群运行监控"
    footer: label("footer", 120) = "ClusterLens"
    theme: described(
        Literal["system", "light", "dark"], "ui.theme 或 ui.palette 无效"
    ) = "system"
    palette: described(
        Literal["green", "blue", "purple", "amber", "teal", "rose"],
        "ui.theme 或 ui.palette 无效",
    ) = "green"
    icon: str | None = None

    @model_validator(mode="before")
    @classmethod
    def known_fields(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.model_fields):
            invalid("ui 必须是仅包含受支持字段的 JSON 对象")
        return value

    @field_validator("title", "name", "mark", "avatar", "workspace", "heading")
    @classmethod
    def nonblank(cls, value, info):
        if not value.strip():
            invalid(cls.model_fields[info.field_name].json_schema_extra["x-error"])
        return value

    @field_validator("icon", mode="before")
    @classmethod
    def safe_png(cls, value):
        if value is not None:
            from ..ui import icon_bytes

            try:
                icon_bytes(value)
            except ValueError as exc:
                invalid(str(exc))
        return value


DEFAULTS = UIConfig().model_dump()
