"""UI package — Chalk design system for the white-label BDR dashboard."""
from app.ui.theme import inject_css
from app.ui.layout import render_sidebar, render_main, render_empty

__all__ = ["inject_css", "render_sidebar", "render_main", "render_empty"]
