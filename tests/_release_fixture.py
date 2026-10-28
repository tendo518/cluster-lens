"""One real, offline-built release per test process; no production state or downloads."""

import atexit
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from project_tools.release import build

PROJECT = Path(__file__).resolve().parents[1]
_TEMP = None


def shared_release():
    global _TEMP
    if _TEMP is None:
        _TEMP = tempfile.TemporaryDirectory(prefix="clusterlens-test-release-")
        atexit.register(_TEMP.cleanup)
        build(Path(_TEMP.name) / "release")
    return Path(_TEMP.name) / "release"


def install_runtime(directory, release=None):
    """Integration test only: the application never imports Ansible's installer."""
    directory = Path(directory)
    directory.mkdir(parents=True)
    bundle = directory / "bundle"
    bundle.mkdir()
    with tarfile.open((release or shared_release()) / "server.tar.gz") as archive:
        archive.extractall(bundle, filter="data")
    environment = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "CLUSTERLENS_VIEWER_TOKEN",
            "CLUSTERLENS_RELEASE_ID",
        }
        and not k.startswith("PIP_")
    }
    # Even deliberately poisoned index configuration cannot make installs online.
    environment.update(
        PIP_CONFIG_FILE=os.devnull,
        PIP_INDEX_URL="http://127.0.0.1:9/unusable",
        PYTHONDONTWRITEBYTECODE="1",
    )
    venv = directory / "venv"
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", str(venv)],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
    )
    python = venv / "bin/python"
    for subdir, requirements in (
        ("bootstrap", "bootstrap-requirements.txt"),
        ("wheels", "requirements.txt"),
    ):
        subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--only-binary=:all:",
                "--require-hashes",
                "--find-links",
                str(bundle / subdir),
                "-r",
                str(bundle / requirements),
            ],
            cwd=directory,
            env=environment,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [str(python), "-I", "-m", "pip", "--isolated", "check"],
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
    )
    return python
