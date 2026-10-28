"""Load the independently shipped Python script for unit tests only."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPORTER = Path(__file__).resolve().parents[1] / "node_reporter/python/reporter.py"
spec = spec_from_file_location("clusterlens_test_reporter", REPORTER)
remote_probe = module_from_spec(spec)
spec.loader.exec_module(remote_probe)
