"""Deterministic, dependency-free release artifacts. No config, tests or deployment code."""

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

from monitor.config import CONFIG_VERSION

from .files import private_write

PROJECT = Path(__file__).resolve().parents[1]
STATIC_SUFFIXES = {".html", ".js", ".css", ".svg"}
EXCLUDED_DIRECTORIES = {
    "__pycache__",
    "tests",
    "docs",
    "data",
    "config",
    "node_modules",
}


def runtime_files(directory, suffixes, excluded=()):
    """Walk only runtime source/resource trees, never linked or private directories."""
    if directory.is_symlink():
        raise ValueError("发布包不允许符号链接：" + str(directory.relative_to(PROJECT)))
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name in excluded:
            continue
        if path.is_symlink():
            raise ValueError("发布包不允许符号链接：" + str(path.relative_to(PROJECT)))
        if path.is_dir():
            if path.name not in EXCLUDED_DIRECTORIES:
                yield from runtime_files(path, suffixes)
        elif path.is_file() and path.suffix in suffixes:
            yield path


def server_bundle():
    package = PROJECT / "monitor"
    files = [
        *runtime_files(package, {".py"}, excluded={"static"}),
        *runtime_files(package / "static", STATIC_SUFFIXES),
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
    for path in (PROJECT / "agents", agent.parent, agent):
        if path.is_symlink():
            raise ValueError("发布包不允许符号链接：" + str(path.relative_to(PROJECT)))
    for name in (
        "monitor/__init__.py",
        "monitor/__main__.py",
        "monitor/static/index.html",
    ):
        if not (PROJECT / name).is_file():
            raise ValueError("中心发布缺少运行入口：" + name)
    files = {
        "server.tar.gz": server_bundle(),
        "agent.py": agent.read_bytes(),
    }
    manifest = {
        "format_version": 1,
        "application": "hqlab-monitor",
        "config_version": CONFIG_VERSION,
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
