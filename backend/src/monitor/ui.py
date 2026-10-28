"""Validated public presentation settings and safe server-side HTML rendering."""

import base64
import binascii
import html
import json
import re
import struct
from pathlib import Path

from .schemas.common import validate
from .schemas.ui import DEFAULTS, PALETTES, THEMES, UIConfig

__all__ = [
    "DEFAULTS",
    "PALETTES",
    "THEMES",
    "favicon",
    "icon_bytes",
    "load_ui",
    "parse_ui",
    "render_page",
]

MAX_ICON_BYTES = 64 * 1024


def icon_bytes(value):
    """Accept only small, bounded PNGs; no URLs, active SVG or arbitrary file types."""
    prefix = "data:image/png;base64,"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError("ui.icon 必须是 PNG 的 data:image/png;base64 URI")
    if len(value) > len(prefix) + ((MAX_ICON_BYTES + 2) // 3) * 4:
        raise ValueError("ui.icon 不得超过 64 KiB")
    try:
        raw = base64.b64decode(value[len(prefix) :], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("ui.icon 的 Base64 无效") from exc
    if (
        not 45 <= len(raw) <= MAX_ICON_BYTES
        or raw[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        or raw[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82"
    ):
        raise ValueError("ui.icon 必须是完整 PNG 文件")
    width, height = struct.unpack(">II", raw[16:24])
    if not 1 <= width <= 1024 or not 1 <= height <= 1024:
        raise ValueError("ui.icon 尺寸应为 1—1024 像素")
    return raw


def parse_ui(value):
    return validate(UIConfig, value).model_dump()


def load_ui(path):
    """Tool input may reference a PNG relative to the UI file; runtime config cannot."""
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or "icon_file" not in value:
        return parse_ui(value)
    if "icon" in value:
        raise ValueError("icon 和 icon_file 不能同时设置")
    source = value.pop("icon_file")
    if not isinstance(source, str) or not source:
        raise ValueError("icon_file 应为 PNG 文件路径")
    with (path.parent / source).open("rb") as stream:
        raw = stream.read(MAX_ICON_BYTES + 1)
    value["icon"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    return parse_ui(value)


def favicon(ui):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="17" fill="#286b4f"/>'
        '<text x="32" y="44" text-anchor="middle" font-family="sans-serif" '
        'font-weight="700" font-size="40" fill="#fff">'
        + html.escape(ui["mark"])
        + "</text></svg>"
    ).encode("utf-8")


def render_page(template, ui):
    values = {key: html.escape(str(value), quote=True) for key, value in ui.items()}
    values["icon_url"] = "/brand-icon.png" if ui["icon"] is not None else "/favicon.svg"
    values["icon_type"] = "image/png" if ui["icon"] is not None else "image/svg+xml"
    values["logo"] = (
        '<img class="brand-image" src="/brand-icon.png" alt="">'
        if ui["icon"] is not None
        else values["mark"] + "<span></span>"
    )
    # One substitution pass: user text is never interpreted as another placeholder.
    return re.sub(
        r"\{\{ui\.([a-z_]+)\}\}", lambda match: values[match[1]], template
    ).encode("utf-8")
