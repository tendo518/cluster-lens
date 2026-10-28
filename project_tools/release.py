"""Reproducible format-2 release assembly from prepared frontend and locked wheels.

This command is deliberately offline: dependency acquisition and npm installation
are separate, explicit commands. No venv, credentials, state or deployment code is
packaged. The controller owns installation; this module owns the artifact contract.
"""

import ast
import fcntl
import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from monitor.config import CONFIG_VERSION
from monitor.schemas.gpu import GPU_PROVIDERS

from .assets import assemble
from .files import private_write
from .wheels import (
    TARGET,
    require_build_python,
    sha256,
    supported_tags,
    target_environment,
    verify_cache,
)

PROJECT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    "__pycache__",
    "tests",
    "docs",
    "data",
    "config",
    "node_modules",
}


def runtime_files(directory, suffixes, excluded=()):
    """Validate the nested runtime tree without following links or local state."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("发布包不允许符号链接：" + str(directory))
    if not directory.is_dir():
        return
    ignored = set(excluded) | EXCLUDED_DIRECTORIES
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name in ignored:
            continue
        if path.is_symlink():
            raise ValueError("发布包不允许符号链接：" + str(path))
        if path.is_dir():
            yield from runtime_files(path, suffixes)
        elif path.is_file() and path.suffix in suffixes:
            yield path


def check_source(project):
    package = project / "backend/src/monitor"
    for name in ("__init__.py", "__main__.py"):
        if not (package / name).is_file():
            raise ValueError("中心发布缺少运行入口：monitor/" + name)
    for parent in (project / "backend", package.parent, package):
        if parent.is_symlink():
            raise ValueError("发布包不允许符号链接：" + str(parent))
    for path in runtime_files(package, {".py"}, {"static"}):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 13))
    reporter = project / "node_reporter/python/reporter.py"
    for path in (reporter.parent.parent, reporter.parent, reporter):
        if path.is_symlink():
            raise ValueError("发布包不允许符号链接：" + str(path))
    body = reporter.read_bytes()
    ast.parse(body.decode(), feature_version=(3, 6))
    return body


def wheel_metadata(filename, content):
    """Reject malformed/foreign wheels and inspect dependency metadata offline."""
    from packaging.utils import canonicalize_name, parse_wheel_filename

    name, version, _build, tags = parse_wheel_filename(filename)
    if not tags.intersection(supported_tags()):
        raise ValueError("wheel 不支持声明的中心目标：" + filename)
    with zipfile.ZipFile(io.BytesIO(content)) as wheel:
        infos = wheel.infolist()
        names = [entry.filename for entry in infos]
        if (
            len(names) != len(set(names))
            or sum(entry.file_size for entry in infos) > 512 * 1024 * 1024
        ):
            raise ValueError("wheel 含重复成员或解压大小过大")
        for entry in infos:
            path = Path(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("wheel 含不安全路径或符号链接")
        metadata_files = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1 or wheel.testzip() is not None:
            raise ValueError("wheel 元数据或 CRC 无效")
        metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))
        if canonicalize_name(metadata["Name"]) != name or metadata["Version"] != str(
            version
        ):
            raise ValueError("wheel 元数据与文件名不一致")
    return name, str(version), metadata


def verify_dependencies(files):
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name

    packages = {}
    for path, content in files.items():
        name, version, metadata = wheel_metadata(Path(path).name, content)
        if name in packages:
            raise ValueError("发布含重复的运行依赖：" + name)
        if not SpecifierSet(metadata.get("Requires-Python", "")).contains("3.13.0"):
            raise ValueError("wheel 的 Python 要求与目标不兼容：" + name)
        packages[name] = (version, metadata)
    for name, (_version, metadata) in packages.items():
        for value in metadata.get_all("Requires-Dist", []):
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate(
                target_environment()
            ):
                continue
            dependency = packages.get(canonicalize_name(requirement.name))
            if (
                requirement.url
                or requirement.extras
                or not dependency
                or not requirement.specifier.contains(dependency[0])
            ):
                raise ValueError(
                    "离线依赖集合不完整或不兼容：" + name + " → " + str(requirement)
                )
    return packages


def archive_bytes(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), 0o644, 0
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(stream.getvalue(), mtime=0)


def source_revision(project):
    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    result = git("rev-parse", "HEAD")
    if result.returncode:
        return "unversioned"
    dirty = git("status", "--porcelain").stdout.strip()
    return result.stdout.strip() + ("-dirty" if dirty else "")


def build_wheel(project, output):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"}
        and not key.startswith(("HATCH_", "PIP_"))
    }
    environment.update(
        SOURCE_DATE_EPOCH="315532800", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "hatchling",
            "build",
            "-t",
            "wheel",
            "-d",
            str(output),
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            "后端 wheel 构建失败；请先 uv sync --frozen --group build："
            + result.stderr[-2000:]
        )
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("后端构建必须恰好生成一个 wheel")
    return wheels[0].name, wheels[0].read_bytes()


def build(directory, wheelhouse=None, frontend=None):
    require_build_python()
    project = PROJECT
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.resolve() in (project, *project.parents):
        raise ValueError("发布输出不能覆盖源码根或是符号链接")
    for source in ("backend", "frontend", "node_reporter", "project_tools"):
        if directory.resolve().is_relative_to(project / source):
            raise ValueError("发布输出不能写入组件源码目录")
    reporter = check_source(project)
    files, selected = verify_cache(wheelhouse or project / ".build/wheels", project)
    cache = project / ".build"
    cache.mkdir(exist_ok=True)
    with (
        (cache / "build.lock").open("a") as lock,
        tempfile.TemporaryDirectory(prefix="clusterlens-wheel-") as temporary,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX)
        assemble(frontend, project)
        name, wheel = build_wheel(project, Path(temporary))
    name_in_bundle = "wheels/" + name
    files[name_in_bundle] = wheel
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    package_name, version, _wheel_info = wheel_metadata(name, wheel)
    if package_name != "cluster-lens" or version != metadata["version"]:
        raise ValueError("构建出的应用 wheel 标识错误")
    runtime = {name: body for name, body in files.items() if name.startswith("wheels/")}
    verify_dependencies(runtime)
    requirements = []
    for path, body in sorted(runtime.items()):
        package, version, _info = wheel_metadata(Path(path).name, body)
        requirements.append(f"{package}=={version} --hash=sha256:{sha256(body)}\n")
    bootstrap = [
        (name, entry)
        for name, entry in selected.items()
        if name.startswith("bootstrap/")
    ]
    if len(bootstrap) != 1 or bootstrap[0][1]["name"] != "pip":
        raise ValueError("必须分发且只分发一个锁定的 pip 安装器")
    pip_path, pip = bootstrap[0]
    wheel_metadata(Path(pip_path).name, files[pip_path])
    files["requirements.txt"] = "".join(requirements).encode()
    files["bootstrap-requirements.txt"] = (
        f"pip=={pip['version']} --hash=sha256:{pip['sha256']}\n".encode()
    )
    bundle = archive_bytes(files)
    # Artifact name is the existing format-2 compatibility contract.
    artifacts = {"server.tar.gz": bundle, "agent.py": reporter}
    manifest = {
        "format_version": 2,
        "application": "hqlab-monitor",
        "version": metadata["version"],
        "config_version": CONFIG_VERSION,
        "report_version": 1,
        "source": {
            "revision": source_revision(project),
            "uv_lock_sha256": sha256((project / "uv.lock").read_bytes()),
            "frontend_lock_sha256": sha256(
                (project / "frontend/package-lock.json").read_bytes()
            ),
        },
        "files": {
            name: {"sha256": sha256(body), "size": len(body)}
            for name, body in artifacts.items()
        },
        "agents": {
            "python": {
                "artifact": "agent.py",
                "minimum_python": "3.6",
                "gpu_providers": list(GPU_PROVIDERS),
                "config_version": 1,
                "report_version": 1,
            }
        },
        "center": {
            "installation": "venv-ensurepip-pip",
            "module": "monitor",
            "target": TARGET,
            "requirements": "requirements.txt",
            "bootstrap_requirements": "bootstrap-requirements.txt",
            "members": {
                name: {"sha256": sha256(body), "size": len(body)}
                for name, body in sorted(files.items())
            },
        },
    }
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, content in artifacts.items():
        private_write(directory / name, content)
    # Manifest last: concurrent readers reject rather than use a mixed release.
    private_write(
        directory / "release.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    return manifest
