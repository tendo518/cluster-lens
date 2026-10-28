"""Version 1 node report: the runtime validator and offline schema source.

Unknown fields are ignored, not trusted as host identity. Missing optional metrics
become null, while explicit null lists/strings and coerced numbers are rejected.
Freshness and configured interval are validated with service-provided context.
"""

import uuid
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .common import WireModel, array, described, integer, invalid, number, text, version

REPORT_VERSION = 1
MAX_REPORT_BYTES = 1024 * 1024


class Disk(WireModel):
    mount: text("mount", 1024)
    total_bytes: number("total_bytes", 2**60, nullable=False)
    used_bytes: number("used_bytes", 2**60, nullable=False)
    available_bytes: number("available_bytes", 2**60, nullable=False)
    percent: number("percent", 100, nullable=False)

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict) or not value:
            invalid("无效的 disk")
        return value


class System(WireModel):
    cpu_percent: number("cpu_percent", 100) = None
    memory_percent: number("memory_percent", 100) = None
    swap_percent: number("swap_percent", 100) = None
    memory_total_bytes: number("memory_total_bytes", 2**60) = None
    memory_used_bytes: number("memory_used_bytes", 2**60) = None
    swap_total_bytes: number("swap_total_bytes", 2**60) = None
    swap_used_bytes: number("swap_used_bytes", 2**60) = None
    uptime_seconds: number("uptime_seconds", 10**12) = None
    cpu_count: number("cpu_count", 65536) = None
    network_rx_bytes_s: number("network_rx_bytes_s", 10**16) = None
    network_tx_bytes_s: number("network_tx_bytes_s", 10**16) = None
    disk_read_bytes_s: number("disk_read_bytes_s", 10**16) = None
    disk_write_bytes_s: number("disk_write_bytes_s", 10**16) = None
    hostname: text("hostname", 255) = "—"
    os: text("os", 255) = "—"
    kernel: text("kernel", 255) = "—"
    load: array(number("load", 10**9, nullable=False), "load", 3) = Field(
        default_factory=list
    )
    disks: array(Disk, "disks", 256) = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict) or not value:
            invalid("成功采集的上报必须包含 system")
        return value


class Process(WireModel):
    pid: integer("无效的进程 PID", 1, 2**31)
    name: text("process name", 2048)
    user: text("process user", 255, nullable=True) = None
    type: text("process type", 32) = "—"
    memory_mb: number("process memory", 10**9) = None

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict):
            invalid("进程数据无效或超过上限")
        return value


class DCGMMetrics(WireModel):
    """Additional current-snapshot telemetry; never replaces utilization/history.

    Ratios have already been converted to percentages by the Node Reporter.
    Missing/unsupported/DCGM blank values are null, not measured zeroes.
    """

    sm_active_percent: number("sm_active_percent", 100) = None
    sm_occupancy_percent: number("sm_occupancy_percent", 100) = None
    tensor_active_percent: number("tensor_active_percent", 100) = None
    dram_active_percent: number("dram_active_percent", 100) = None
    pcie_rx_bytes_s: number("pcie_rx_bytes_s", 10**16) = None
    pcie_tx_bytes_s: number("pcie_tx_bytes_s", 10**16) = None
    nvlink_rx_bytes_s: number("nvlink_rx_bytes_s", 10**16) = None
    nvlink_tx_bytes_s: number("nvlink_tx_bytes_s", 10**16) = None
    memory_temperature: number("memory_temperature", 200) = None
    sm_clock_mhz: number("sm_clock_mhz", 10**6) = None
    memory_clock_mhz: number("memory_clock_mhz", 10**6) = None
    ecc_sbe_volatile: integer("无效的 DCGM ECC 计数", 0, 2**53 - 1) | None = None
    ecc_dbe_volatile: integer("无效的 DCGM ECC 计数", 0, 2**53 - 1) | None = None
    xid_last: integer("无效的 DCGM XID", 0, 2**31 - 1) | None = None


class GPU(WireModel):
    index: integer("GPU index 无效或重复", 0, 1023)
    name: text("GPU name", 255)
    uuid: text("GPU uuid", 128, nullable=True) = None
    driver: text("driver", 128, nullable=True) = None
    utilization: number("utilization", 100) = None
    memory_percent: number("memory_percent", 100) = None
    fan_percent: number("fan_percent", 100) = None
    memory_used_mb: number("memory_used_mb", 10**9) = None
    memory_total_mb: number("memory_total_mb", 10**9) = None
    temperature: number("temperature", 200) = None
    power_w: number("power_w", 100000) = None
    power_limit_w: number("power_limit_w", 100000) = None
    processes: array(Process, "processes", 4096)
    processes_available: bool = True
    dcgm: DCGMMetrics | None = None

    @model_validator(mode="after")
    def unavailable_processes(self):
        if not self.processes_available and self.processes:
            invalid("不可读取的进程列表不能包含进程")
        return self

    @model_validator(mode="before")
    @classmethod
    def object_required(cls, value):
        if not isinstance(value, dict):
            invalid("GPU index 无效或重复")
        return value


INTERVAL_ERROR = "节点 interval 与中心配置不一致，请重新生成配置并部署"
VERSION_ERROR = "上报必须是 version=1 的 JSON 对象"


class Report(WireModel):
    version: version(VERSION_ERROR)
    report_id: Annotated[
        text("report_id", 36), Field(json_schema_extra={"format": "uuid"})
    ]
    collected_at: number("collected_at", 10**12, nullable=False)
    interval: integer(INTERVAL_ERROR, 5, 3600)
    collection_ms: number("collection_ms", 3600000) = None
    error: text("error", 600, nullable=True) = None
    warnings: array(text("warnings", 300), "warnings", 32) = Field(default_factory=list)
    system: described(System | None, "成功采集的上报必须包含 system")
    gpus: array(GPU, "gpus", 128)
    host: text("host", 128, minimum=1)
    gpu_provider: Literal["nvidia-smi", "dcgm"] | None = None

    @model_validator(mode="after")
    def metric_source(self):
        if self.gpu_provider != "dcgm" and any(
            gpu.dcgm is not None for gpu in self.gpus
        ):
            invalid("DCGM 指标必须注明 dcgm 采集来源")
        return self

    @model_validator(mode="before")
    @classmethod
    def object_and_failure(cls, value):
        if not isinstance(value, dict):
            invalid(VERSION_ERROR)
        # Failed collections deliberately discard metrics, even malformed ones.
        # Do not mutate the caller's object or accept a malformed error field.
        if isinstance(value.get("error"), str) and value["error"]:
            value = {**value, "system": None, "gpus": []}
        return value

    @field_validator("report_id")
    @classmethod
    def uuid_string(cls, value):
        try:
            uuid.UUID(value)
        except ValueError:
            invalid("report_id 必须是 UUID")
        # Keep spelling intact: report IDs are opaque deduplication keys.
        return value

    @field_validator("collected_at")
    @classmethod
    def fresh_sample(cls, value, info):
        context = info.context or {}
        if "now" in context and abs(context["now"] - value) > max(
            60, context["interval"] * 2
        ):
            invalid("采样时间过旧或位于未来，请同步节点和中心服务器时钟后重新采样")
        return value

    @field_validator("interval")
    @classmethod
    def configured_interval(cls, value, info):
        context = info.context or {}
        if "interval" in context and value != context["interval"]:
            invalid(INTERVAL_ERROR)
        return value

    @field_validator("system", mode="wrap")
    @classmethod
    def successful_system(cls, value, handler, info):
        if info.data.get("error"):
            return None
        if not isinstance(value, dict) or not value:
            invalid("成功采集的上报必须包含 system")
        return handler(value)

    @field_validator("gpus")
    @classmethod
    def unique_and_bounded(cls, value):
        if len({gpu.index for gpu in value}) != len(value):
            invalid("GPU index 无效或重复")
        if sum(len(gpu.processes) for gpu in value) > 4096:
            invalid("进程数据无效或超过上限")
        return value

    def snapshot(self):
        result = self.model_dump(exclude={"version", "host", "interval"})
        # Additive v1 fields do not change legacy normalized snapshot shapes.
        if self.gpu_provider is None:
            result.pop("gpu_provider", None)
        for gpu, source in zip(result["gpus"], self.gpus):
            if source.dcgm is None:
                gpu.pop("dcgm", None)
            if "processes_available" not in source.model_fields_set:
                gpu.pop("processes_available", None)
        if self.error:
            result["system"], result["gpus"] = {}, []
        return result
