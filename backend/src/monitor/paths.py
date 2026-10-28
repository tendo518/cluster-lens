"""Writable state lives outside installed application code. Never move old history."""

import os
from pathlib import Path


def state_directory():
    configured = os.environ.get("XDG_STATE_HOME")
    # XDG specifies absolute paths; ignore invalid relative environment values.
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".local" / "state"
    )
    return base / "clusterlens"


def default_database(*, demo=False):
    return state_directory() / ("monitor-demo.sqlite3" if demo else "monitor.sqlite3")
