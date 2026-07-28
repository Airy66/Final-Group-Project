"""Compatibility WSGI entry point for academic submission tooling.

The canonical production application is ``precision_app:app``. This module
creates no Flask instance, routes, clients, or startup side effects of its own.
"""

from dotenv import load_dotenv

load_dotenv(override=True)

from precision_app import app


__all__ = ["app"]
