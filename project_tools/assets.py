"""Assemble already-built, public frontend resources into the runtime package."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SUFFIXES = {".html", ".js", ".css", ".svg", ".png", ".webp", ".woff", ".woff2"}
REQUIRED = {"index.html", "app.js", "appearance.js", "styles.css", "palettes.css"}


def public_files(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("前端资源目录缺失或为符号链接，请先运行 npm run build")
    result = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if path.is_symlink():
            raise ValueError("前端资源不允许符号链接：" + str(relative))
        if any(part.startswith(".") for part in relative.parts):
            raise ValueError("前端资源不允许隐藏文件：" + str(relative))
        if path.is_dir():
            continue
        if (
            not path.is_file()
            or path.suffix not in SUFFIXES
            or path.stat().st_size > 8 * 1024 * 1024
        ):
            raise ValueError("前端资源含非公开或过大的文件：" + str(relative))
        result[str(relative)] = path.read_bytes()
    if REQUIRED - result.keys():
        raise ValueError(
            "前端构建缺少运行资源：" + ", ".join(sorted(REQUIRED - result.keys()))
        )
    page = result["index.html"].decode("utf-8")
    if (
        "{{ui.title}}" not in page
        or "{{ui.theme}}" not in page
        or "{{ui.logo}}" not in page
    ):
        raise ValueError("前端构建丢失公开品牌占位符")
    appearance = page.find('src="/appearance.js"')
    style = page.find('href="/styles.css"')
    if appearance < 0 or style < 0 or appearance > style:
        raise ValueError("外观初始化必须在样式前以阻塞脚本执行")
    return result


def verify_receipt(frontend, assets):
    """Verify the TypeScript builder's exact source/output receipt, not mtimes."""
    frontend = Path(frontend)
    receipt_path = frontend / "build-receipt.json"
    if receipt_path.is_symlink():
        raise ValueError("前端构建收据不能是符号链接")
    try:
        receipt = json.loads(receipt_path.read_text())
    except OSError as exc:
        raise ValueError("缺少前端构建收据，请重新运行 npm run build") from exc
    paths = [
        frontend / name
        for name in (
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "vite.config.ts",
            "vite.appearance.config.ts",
        )
    ]
    for name in ("src", "public", "scripts"):
        root = frontend / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError("前端源码目录缺失或为符号链接")
        for path in root.rglob("*"):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError("前端源码包含非普通文件")
            if path.is_file():
                paths.append(path)
    inputs = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("前端构建输入缺失或为符号链接")
        inputs[path.relative_to(frontend).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    outputs = {name: hashlib.sha256(body).hexdigest() for name, body in assets.items()}
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("schema")) is not int
        or receipt
        != {"schema": 1, "algorithm": "sha256", "inputs": inputs, "dist": outputs}
    ):
        raise ValueError("前端源码或产物已变化，请重新运行 npm run build")


def assemble(directory=None, project=PROJECT):
    project = Path(project)
    files = public_files(directory or project / "frontend/dist")
    verify_receipt(project / "frontend", files)
    target = project / "backend/src/monitor/static"
    for parent in (project / "backend", target.parent.parent, target.parent, target):
        if parent.is_symlink():
            raise ValueError("后端资源目录不能是符号链接")
    if target.exists():
        # This is a generated directory, but do not delete unexpected user files.
        public_files(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".static-", dir=target.parent))
    previous = temporary.with_name(temporary.name + "-previous")
    try:
        for name, content in files.items():
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if target.exists():
            os.replace(target, previous)
        try:
            os.replace(temporary, target)
        except BaseException:
            if previous.exists():
                os.replace(previous, target)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if previous.exists():
            shutil.rmtree(previous)
    return files
