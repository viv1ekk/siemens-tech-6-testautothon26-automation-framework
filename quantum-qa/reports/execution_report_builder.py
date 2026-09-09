"""Orchestrates building the HTML execution report from raw ui_execution data."""

from pathlib import Path

from reports.ai_failure_summarizer import summarize_failures
from reports.execution_report_renderer import render_execution_report_html
from reports.report_data import parse_execution_report


def build_execution_report(execution_data: dict, artifact_root: Path) -> Path:
    report_data = parse_execution_report(execution_data)
    ai_summary = summarize_failures(execution_data, report_data["failures"])
    html = render_execution_report_html(report_data, ai_summary)

    artifact_root.mkdir(parents=True, exist_ok=True)
    output_path = artifact_root / "execution_report.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path
