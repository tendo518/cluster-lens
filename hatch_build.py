"""Wheel gate: generated assets are mandatory; secrets/state are never package data."""

import ast
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        package = Path(self.root) / "backend/src/monitor"
        static = package / "static"
        required = (
            "index.html",
            "app.js",
            "appearance.js",
            "styles.css",
            "palettes.css",
        )
        if any(not (static / name).is_file() for name in required):
            raise ValueError(
                "Build frontend and run project_tools assets before building the wheel"
            )
        allowed_assets = {
            ".html",
            ".js",
            ".css",
            ".svg",
            ".png",
            ".webp",
            ".woff",
            ".woff2",
        }
        for path in package.rglob("*"):
            relative = path.relative_to(package)
            if path.is_symlink():
                raise ValueError(
                    "Runtime package must not contain symlinks: " + str(relative)
                )
            if "__pycache__" in relative.parts or path.is_dir():
                continue
            if relative.parts[0] == "static":
                if path.suffix not in allowed_assets or any(
                    p.startswith(".") for p in relative.parts
                ):
                    raise ValueError(
                        "Non-public file in runtime assets: " + str(relative)
                    )
            elif path.suffix == ".py":
                if any(
                    part in {"tests", "docs", "data", "config"} or part.startswith(".")
                    for part in relative.parts
                ):
                    raise ValueError(
                        "Unexpected source package subtree: " + str(relative)
                    )
                ast.parse(path.read_text(), filename=str(path), feature_version=(3, 13))
            else:
                raise ValueError("Unexpected runtime package data: " + str(relative))
        # These generated, explicitly validated files are intentionally gitignored.
        build_data["artifacts"].append("backend/src/monitor/static/**")
