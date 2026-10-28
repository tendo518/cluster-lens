"""Bounded public assets from package resources or an explicit development directory."""

import os
import re
import stat
from importlib.resources import files
from pathlib import Path

PUBLIC_ASSETS = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/appearance.js": ("appearance.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/palettes.css": ("palettes.css", "text/css; charset=utf-8"),
}
GENERATED_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
MAX_ASSET_BYTES = 8 * 1024 * 1024
SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class Assets:
    def __init__(self, directory=None):
        self.root = (
            Path(directory).absolute()
            if directory is not None
            else files("monitor").joinpath("static")
        )

    def read(self, name):
        parts = name.split("/")
        if (
            len(name) > 240
            or len(parts) > 4
            or not all(SAFE_PART.fullmatch(p) for p in parts)
        ):
            raise ValueError("无效的静态资源路径")
        if isinstance(self.root, Path):
            # dir_fd + O_NOFOLLOW protects against both linked parents and races.
            # No FileResponse: its deferred open could follow a replaced symlink.
            root = self.root.absolute()
            descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in (*root.parts[1:], *parts[:-1]):
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = child
                fd = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=descriptor,
                )
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES:
                        raise ValueError("静态资源必须是有界的普通文件")
                    body = stream.read(MAX_ASSET_BYTES + 1)
            finally:
                os.close(descriptor)
        else:
            # importlib Traversable also supports non-filesystem package loaders.
            with self.root.joinpath(*parts).open("rb") as stream:
                body = stream.read(MAX_ASSET_BYTES + 1)
        if len(body) > MAX_ASSET_BYTES:
            raise ValueError("静态资源过大")
        return body

    def validate(self):
        for name in ("index.html", *(entry[0] for entry in PUBLIC_ASSETS.values())):
            if not self.read(name):
                raise ValueError("静态资源不完整")
        self.read("index.html").decode("utf-8")

    def response(self, path):
        entry = PUBLIC_ASSETS.get(path)
        if entry is None:
            name = path.removeprefix("/")
            if not name.startswith("assets/"):
                return None
            content_type = GENERATED_TYPES.get(Path(name).suffix)
            if content_type is None:
                return None
            entry = (name, content_type)
        try:
            return self.read(entry[0]), entry[1]
        except (OSError, ValueError):
            return None
