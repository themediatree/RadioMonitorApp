"""
Shared Jinja2 Templates instance.

Importing this in every route module ensures we don't accidentally create
multiple Templates() with diverging context globals.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
