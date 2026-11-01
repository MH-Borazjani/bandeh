"""Template rendering configuration for Jinja2."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Path to the templates directory (app/e-commerce/templates)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

__all__ = ["templates", "TEMPLATES_DIR"]

