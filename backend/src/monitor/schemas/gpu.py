"""Optional GPU collection settings. No library/daemon is installed by this model.

The standalone reporter independently validates this same small contract using
only the Python 3.6 standard library; cross-boundary tests keep both in agreement.
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, ValidationError, field_validator, model_validator

from .common import WireModel, integer, invalid

GPU_PROVIDERS = ("nvidia-smi", "dcgm")
GPU_CONFIG_ERROR = "GPU 采集配置无效"
DEFAULT_DCGM_URL = "http://127.0.0.1:9400/metrics"


class GPUConfig(WireModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    provider: Literal["nvidia-smi", "dcgm"] = "nvidia-smi"
    url: str = DEFAULT_DCGM_URL
    timeout: integer(GPU_CONFIG_ERROR, 1, 8) = 3
    processes: Literal["nvidia-smi", "none"] = "nvidia-smi"

    @field_validator("url")
    @classmethod
    def local_metrics_only(cls, value):
        try:
            parsed = urlsplit(value)
            port = parsed.port
            valid = (
                parsed.scheme == "http"
                and parsed.hostname in ("127.0.0.1", "::1")
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.path == "/metrics"
                and (port is None or 1 <= port <= 65535)
                and not any(
                    c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value
                )
                and "?" not in value
                and "#" not in value
                and re.fullmatch(r"(?:127\.0\.0\.1|\[::1\])(?::[0-9]+)?", parsed.netloc)
                is not None
            )
        except ValueError:
            valid = False
        if not valid:
            invalid(GPU_CONFIG_ERROR)
        return value

    @model_validator(mode="after")
    def provider_options(self):
        if self.provider == "nvidia-smi" and self.model_fields_set - {"provider"}:
            invalid(GPU_CONFIG_ERROR)
        return self

    def settings(self):
        if self.provider == "nvidia-smi":
            return {"provider": self.provider}
        return self.model_dump()


def parse_gpu_config(value):
    """A safe public error never echoes configuration values or URLs."""
    try:
        return GPUConfig.model_validate(value).settings()
    except ValidationError:
        raise ValueError(GPU_CONFIG_ERROR) from None
