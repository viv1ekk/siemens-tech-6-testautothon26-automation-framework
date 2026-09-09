"""Parses ui_execution.json-shaped data into a normalized report model."""


def parse_execution_report(execution_data: dict) -> dict:
    workflow_summary = execution_data.get("workflow_summary", {})
    summary = {
        "planned": workflow_summary.get("planned", 0),
        "executed": workflow_summary.get("executed", 0),
        "passed": workflow_summary.get("passed", 0),
        "failed": workflow_summary.get("failed", 0),
        "skipped": workflow_summary.get("skipped", 0),
    }

    artifact_root = str(execution_data.get("artifact_root", "")).rstrip("/")
    steps = []
    failures = []
    for raw_step in execution_data.get("steps", []):
        details = raw_step.get("details", {}) or {}
        status = raw_step.get("status", "unknown")
        step_record = {
            "stage": raw_step.get("stage"),
            "raw_step": raw_step.get("raw_step", raw_step.get("step", "")),
            "status": status,
            "duration_ms": details.get("duration_ms"),
            "screenshot": _relative_screenshot_path(details.get("screenshot", ""), artifact_root),
            "error": raw_step.get("error", ""),
        }
        steps.append(step_record)
        if status == "failed":
            failures.append(step_record)

    return {
        "title": execution_data.get("title", ""),
        "final_url": execution_data.get("final_url", ""),
        "passed": bool(execution_data.get("passed", False)),
        "summary": summary,
        "steps": steps,
        "failures": failures,
    }


def _relative_screenshot_path(screenshot_path: str, artifact_root: str) -> str:
    if not screenshot_path:
        return ""
    normalized = screenshot_path.replace("\\", "/")
    if artifact_root and normalized.startswith(artifact_root + "/"):
        return normalized[len(artifact_root) + 1:]
    return normalized
