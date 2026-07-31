"""Compatibility WSGI entry point for academic submission tooling.

The canonical production application is ``precision_app:app``. This module
creates no Flask instance, routes, clients, or startup side effects of its own.
"""

import os
import sys

from dotenv import load_dotenv

_test_environment = os.getenv("PRECISION_TESTING") == "true" and "pytest" in sys.modules
load_dotenv(override=True)
if _test_environment:
    os.environ.update(APP_ENV="testing", DEMO_MODE="true")
    os.environ.pop("MONGO_URI", None)

from precision_app import app


__all__ = ["app"]
