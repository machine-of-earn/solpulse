"""Renderers turning a snapshot dict into human- and machine-readable output.

Every renderer takes the same `snapshot/v1` dict the collector produces and is
pure: no network, no clock, no filesystem. That keeps them testable offline and
makes the same snapshot reproducible in JSON, Markdown and HTML.
"""

from .html import render_html
from .markdown import render_markdown

__all__ = ["render_html", "render_markdown"]
