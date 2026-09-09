"""Renders the execution report HTML from normalized report data."""

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def render_execution_report_html(report_data: dict, ai_summary: str | None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("execution_report.html.j2")
    return template.render(
        report=report_data,
        ai_summary=ai_summary,
        generated_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )
