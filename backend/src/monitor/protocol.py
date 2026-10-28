"""Report compatibility boundary; all wire constraints live in Pydantic schemas."""

from .schemas.common import validate
from .schemas.report import MAX_REPORT_BYTES, REPORT_VERSION, Report

__all__ = ["MAX_REPORT_BYTES", "REPORT_VERSION", "validate_report"]


def validate_report(report, interval, now):
    return validate(
        Report, report, context={"interval": interval, "now": now}
    ).snapshot()
