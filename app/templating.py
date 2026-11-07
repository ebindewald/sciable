"""Shared Jinja2 templates instance.

Both the web routes (``app.web``) and the submission endpoint (``app.api``, which
renders an HTML error page for browser clients) need the same template env. It
lives here so ``app.api`` can render without importing ``app.web`` — ``app.web``
already imports ``app.api``, so the reverse would be a circular import.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import REPO_ROOT

templates = Jinja2Templates(directory=str(REPO_ROOT / "app" / "templates"))
