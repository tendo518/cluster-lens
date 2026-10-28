"""Explicit acquisition of uv-locked wheels for one declared center target.

No index resolution or installation occurs here. The lock is the source of URLs,
versions and hashes; only pinned HTTPS PyPI wheel URLs are fetched. Release assembly
uses this cache read-only and never acquires missing dependencies implicitly.
"""

import hashlib
import os
import platform
import ssl
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]
TARGET = {
    "implementation": "cpython",
    "python": "3.13",
    "abi": "cp313",
    "system": "linux",
    "machine": "x86_64",
    "libc": "glibc",
    "glibc_min": "2.17",
    "sqlite_min": "3.25.0",
}
MAX_WHEEL_BYTES = 256 * 1024 * 1024


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def target_environment():
    # Never evaluate target markers using the controller's OS or Python version.
    return {
        "implementation_name": "cpython",
        "implementation_version": "3.13.0",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "platform_python_implementation": "CPython",
        "python_full_version": "3.13.0",
        "python_version": "3.13",
        "sys_platform": "linux",
        "extra": "",
    }


def supported_tags():
    from packaging.tags import compatible_tags, cpython_tags

    platforms = ["manylinux_2_17_x86_64", "manylinux2014_x86_64"]
    return [
        *cpython_tags((3, 13), abis=["cp313"], platforms=platforms),
        *compatible_tags((3, 13), interpreter="cp313", platforms=platforms),
    ]


def locked_wheels(project=PROJECT):
    """Select runtime closure plus pip bootstrap; fail on ambiguous lock variants."""
    from packaging.markers import Marker
    from packaging.utils import canonicalize_name, parse_wheel_filename

    project = Path(project)
    lock = tomllib.loads((project / "uv.lock").read_text())
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    packages = {}
    for package in lock["package"]:
        markers = package.get("resolution-markers", [])
        if markers and not any(
            Marker(m).evaluate(target_environment()) for m in markers
        ):
            continue
        name = canonicalize_name(package["name"])
        if name in packages:
            raise ValueError("目标存在多个锁定版本，不能猜测选择：" + name)
        packages[name] = package
    application = packages.get(canonicalize_name(metadata["name"]))
    if not application or application["version"] != metadata["version"]:
        raise ValueError("项目版本与 uv.lock 不一致，请先更新锁文件")
    selected = {}

    def visit(dependency):
        marker = dependency.get("marker")
        if marker and not Marker(marker).evaluate(target_environment()):
            return
        name = canonicalize_name(dependency["name"])
        if name in selected:
            return
        package = packages.get(name)
        if not package or package.get("source") != {
            "registry": "https://pypi.org/simple"
        }:
            raise ValueError("发布只接受明确锁定的 PyPI wheel：" + name)
        if dependency.get("version", package["version"]) != package["version"]:
            raise ValueError("目标依赖与锁定版本不一致：" + name)
        selected[name] = package
        for child in package.get("dependencies", []):
            visit(child)

    for dependency in application["dependencies"]:
        visit(dependency)
    runtime_names = set(selected)
    visit({"name": "pip"})
    tags = {tag: index for index, tag in enumerate(supported_tags())}
    result = {}
    for name, package in sorted(selected.items()):
        choices = []
        for wheel in package.get("wheels", []):
            parsed = urlsplit(wheel["url"])
            filename = unquote(parsed.path.rsplit("/", 1)[-1])
            if (
                parsed.scheme != "https"
                or parsed.netloc != "files.pythonhosted.org"
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("wheel 下载地址必须是锁定的官方 HTTPS 地址")
            distribution, version, _build, wheel_tags = parse_wheel_filename(filename)
            if distribution != name or str(version) != package["version"]:
                raise ValueError("wheel 文件名与锁定元数据不一致")
            compatible = [tags[tag] for tag in wheel_tags if tag in tags]
            if compatible:
                choices.append((min(compatible), filename, wheel))
        if not choices:
            raise ValueError(
                "目标 CPython 3.13 / Linux x86_64 / glibc 2.17 缺少 wheel：" + name
            )
        _rank, filename, wheel = min(choices)
        if (
            not wheel["hash"].startswith("sha256:")
            or not 0 < wheel["size"] <= MAX_WHEEL_BYTES
        ):
            raise ValueError("无效的锁定 wheel 摘要或大小")
        subdir = "wheels" if name in runtime_names else "bootstrap"
        result[subdir + "/" + filename] = {
            "name": name,
            "version": package["version"],
            "url": wheel["url"],
            "sha256": wheel["hash"].split(":", 1)[1],
            "size": wheel["size"],
        }
    return result


def verify_cache(directory, project=PROJECT):
    """Read only the exact selected cache files; no network and no fallback."""
    directory = Path(directory)
    selected = locked_wheels(project)
    files = {}
    for name, entry in selected.items():
        path = directory / name
        if any(p.is_symlink() for p in (directory, path.parent, path)):
            raise ValueError("wheel 缓存不能包含符号链接：" + name)
        if not path.is_file():
            raise ValueError("缺少已准备的 wheel，请显式运行 prepare-wheels：" + name)
        if path.stat().st_size != entry["size"]:
            raise ValueError("wheel 缓存大小不匹配：" + name)
        body = path.read_bytes()
        if sha256(body) != entry["sha256"]:
            raise ValueError("wheel 缓存摘要不匹配：" + name)
        files[name] = body
    return files, selected


def prepare(directory, project=PROJECT):
    """The only dependency acquisition command; never called by configure/build."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("wheel 缓存目录不能是符号链接")
    selected = locked_wheels(project)
    tls = ssl.create_default_context()
    # Some portable CPython builds expect /etc/ssl/cert.pem, while the approved
    # Linux host publishes its trust store at the Debian/NixOS path instead.
    # Add that system trust only when no explicit SSL trust setting was supplied.
    system_ca = Path("/etc/ssl/certs/ca-certificates.crt")
    if (
        not os.environ.get("SSL_CERT_FILE")
        and not os.environ.get("SSL_CERT_DIR")
        and system_ca.is_file()
    ):
        tls.load_verify_locations(cafile=str(system_ca))
    for name, entry in selected.items():
        path = directory / name
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("wheel 缓存不能包含符号链接：" + name)
        if (
            path.is_file()
            and path.stat().st_size == entry["size"]
            and sha256(path.read_bytes()) == entry["sha256"]
        ):
            continue
        with urlopen(entry["url"], timeout=60, context=tls) as response:
            content = response.read(entry["size"] + 1)
        if len(content) != entry["size"] or sha256(content) != entry["sha256"]:
            raise ValueError("下载的 wheel 与 uv.lock 摘要不匹配：" + name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    verify_cache(directory, project)
    return {"target": TARGET, "wheels": len(selected)}


def require_build_python():
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13):
        raise ValueError("发布构建工具需要独立的 CPython 3.13 应用环境")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("当前发布构建仅验收 Linux x86_64，未实现跨平台发行")
