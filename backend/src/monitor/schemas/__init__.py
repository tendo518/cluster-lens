"""Shared runtime models; JSON Schemas are exportable offline, never via HTTP."""

from .config import CONFIG_VERSION, ServerConfig
from .report import MAX_REPORT_BYTES, REPORT_VERSION, Report
from .ui import UIConfig

__all__ = [
    "CONFIG_VERSION",
    "MAX_REPORT_BYTES",
    "REPORT_VERSION",
    "Report",
    "ServerConfig",
    "UIConfig",
    "export_schemas",
]


def export_schemas():
    """No filesystem or network effects. Build tooling chooses the output path."""
    return {
        "server-config": ServerConfig.model_json_schema(),
        "agent-report": Report.model_json_schema(),
        "ui-config": UIConfig.model_json_schema(),
    }
