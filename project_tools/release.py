"""Deterministic, dependency-free release artifacts. No config, tests or deployment code."""

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from .files import private_write

PROJECT = Path(__file__).resolve().parents[1]
STATIC_SUFFIXES = {".html", ".js", ".css", ".svg"}


def server_bundle():
    package = PROJECT / "monitor"
    files = list(package.glob("*.py")) + [
        path
        for path in (package / "static").rglob("*")
        if path.suffix in STATIC_SUFFIXES
    ]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(files):
            if path.is_symlink():
                raise ValueError(
                    "发布包不允许符号链接：" + str(path.relative_to(PROJECT))
                )
            if not path.is_file():
                continue
            info = tarfile.TarInfo(str(path.relative_to(PROJECT)))
            content = path.read_bytes()
            info.size, info.mode, info.mtime = len(content), 0o644, 0
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(stream.getvalue(), mtime=0)


def build(directory):
    directory = Path(directory)
    # Build everything first; no credentials are ever inspected by this command.
    agent = PROJECT / "agents/python/agent.py"
    if agent.is_symlink():
        raise ValueError("发布包不允许符号链接：agents/python/agent.py")
    files = {
        "server.tar.gz": server_bundle(),
        "agent.py": agent.read_bytes(),
    }
    manifest = {
        "format_version": 1,
        "application": "hqlab-monitor",
        "files": {
            name: {"sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        },
    }
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, content in files.items():
        private_write(directory / name, content)
    private_write(directory / "release.json", json.dumps(manifest, indent=2) + "\n")
    return manifest
