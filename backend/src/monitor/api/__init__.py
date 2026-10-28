"""FastAPI transport. Service and storage know nothing about HTTP or ASGI."""

from .app import create_app

__all__ = ["create_app"]
