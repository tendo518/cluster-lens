"""Load the independently shipped Python script for unit tests only."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agents/python/agent.py"
spec = spec_from_file_location("clusterlens_test_agent", AGENT)
remote_probe = module_from_spec(spec)
spec.loader.exec_module(remote_probe)
