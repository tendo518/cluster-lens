"""Private, atomic writes for local configuration and release artifacts."""

import os
import tempfile
from pathlib import Path


def private_write(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(
                content if isinstance(content, bytes) else content.encode("utf-8")
            )
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
