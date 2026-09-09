"""
QUANTUM-QA Dashboard
Shows the latest framework run, including UI discovery, execution, audit trail,
and release readiness data.
"""

import json
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="QUANTUM-QA Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _latest_run() -> dict:
    run = _load_json(DATA_DIR / "latest_run.json", {})
    if run:
        return run
    return _load_json(ROOT_DIR / "artifacts" / "latest_run.json", {})


def _latest_value(run: dict, key: str, default):
    value = run.get(key, default)
    return value if value is not None else default


def _history() -> list[dict]:
    return _load_json(DATA_DIR / "execution_history.json", [])


def _humanize(value: str) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else "Item"


def _short_text(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _workflow_overview(run: dict) -> dict:
    workflow = run.get("ui_workflow", {})
    execution = run.get("ui_execution", {})
    execution_summary = execution.get("workflow_summary", {})
    actions = workflow.get("actions", [])
    stages = sorted({int(action.get("stage", 0)) for action in actions if action.get("stage")})
    return {
        "planned": int(workflow.get("total_actions", len(actions))),
        "executed": int(execution_summary.get("executed", 0)),
        "passed": int(execution_summary.get("passed", 0)),
        "failed": int(execution_summary.get("failed", 0)),
        "skipped": int(execution_summary.get("skipped", 0)),
        "stage_count": len(stages),
        "sample_steps": [action.get("raw_step", "") for action in actions[:5]],
    }


def _category_for_element(element: dict) -> tuple[str, str]:
    tag = (element.get("tag") or "").lower()
    kind = (element.get("kind") or "").lower()
    name = (element.get("name") or "").lower()
    selector = element.get("selector", {})
    role = selector.get("value", {}).get("role") if selector.get("strategy") == "role" else ""

    if tag in {"input", "textarea", "select"} or kind == "input":
        return "Form Controls", "Can accept user input"
    if tag == "button" or any(term in name for term in ["submit", "save", "login", "continue", "next", "checkout", "buy", "confirm"]):
        return "Primary Actions", "Main action that drives a flow"
    if tag == "a" or role == "link" or kind == "action":
        return "Navigation & Sections", "Navigation entry point or section link"
    return "Visible Content", "Text or content on the page"


def _capability_groups(run: dict) -> list[dict]:
    elements = run.get("ui_discovery", {}).get("elements", [])
    grouped = {}
    order = ["Navigation & Sections", "Form Controls", "Primary Actions", "Visible Content"]

    for element in elements:
        category, description = _category_for_element(element)
        grouped.setdefault(category, {
            "title": category,
            "description": description,
            "items": [],
        })
        grouped[category]["items"].append({
            "label": _humanize(element.get("text") or element.get("name") or element.get("tag")),
            "source": element.get("source", "unknown"),
            "confidence": element.get("confidence", 0),
            "kind": element.get("kind", "unknown"),
        })

    capabilities = []
    for category in order:
        group = grouped.get(category)
        if not group:
            continue
        items = group["items"]
        confidence = round(sum(item["confidence"] for item in items) / len(items), 2) if items else 0
        capabilities.append({
            "title": group["title"],
            "description": group["description"],
            "count": len(items),
            "confidence": confidence,
            "samples": items[:6],
        })

    return capabilities


def _agent_cards(run: dict) -> list[dict]:
    audit_trail = run.get("audit_trail", [])
    latest_by_agent = {}
    for entry in audit_trail:
        agent = entry.get("agent", "Agent")
        latest_by_agent[agent] = entry

    cards = []
    for agent_name in ["UiPipeline", "RiskAnalyzer", "SkillOrchestrator", "TestExecutor", "QualityInspector", "CognitiveDebtMonitor", "Learner", "HumanGate"]:
        entry = latest_by_agent.get(agent_name)
        if not entry:
            continue
        if agent_name == "UiPipeline":
            message = f"Discovered {entry.get('discovered_elements', 0)} interactive items and executed {entry.get('executed_steps', 0)} workflow steps."
        elif agent_name == "RiskAnalyzer":
            message = f"Risk focus is {entry.get('recommended_focus', 'unknown')} with overall score {entry.get('overall_risk', 0):.2f}."
        elif agent_name == "SkillOrchestrator":
            message = f"Planned {entry.get('skills_planned', 0)} checks for intent {entry.get('intent_type', 'unknown')}."
        elif agent_name == "TestExecutor":
            message = f"Ran {entry.get('skills_executed', 0)} checks across {entry.get('total_executions', 0)} executions using the {entry.get('run_profile', 'demo')} profile."
        elif agent_name == "QualityInspector":
            message = f"Found {entry.get('defects', 0)} defects, {entry.get('flakes', 0)} flaky checks, and {entry.get('environment_issues', 0)} environment issues."
        elif agent_name == "CognitiveDebtMonitor":
            message = f"Cognitive debt is {entry.get('severity', 'LOW').lower()} with score {entry.get('debt_score', 0)}."
        elif agent_name == "Learner":
            message = f"Release readiness is {entry.get('release_readiness_score', 0)}/100."
        else:
            message = f"Human decision recorded as {entry.get('decision', 'pending')}."

        cards.append({
            "agent": agent_name,
            "timestamp": entry.get("timestamp", "-"),
            "message": message,
        })

    return cards


def _recommendations(run: dict) -> list[str]:
    summary = _summary(run)
    recommendations = []
    groups = _capability_groups(run)
    group_titles = {group["title"] for group in groups}

    if "Navigation & Sections" in group_titles:
        recommendations.append("Add click-through tests for the main navigation and section links.")
    if "Form Controls" in group_titles:
        recommendations.append("Add field validation tests for the discovered inputs and selectors.")
    if "Primary Actions" in group_titles:
        recommendations.append("Cover the key call-to-action flows end to end.")
    if summary["flake_count"] > 0:
        recommendations.append("Stabilize flaky checks before expanding coverage.")
    if summary["environment_issue_count"] > 0:
        recommendations.append("Tighten runner and device configuration so environment issues do not hide product quality.")
    if summary["debt_severity"] in {"MEDIUM", "HIGH"}:
        recommendations.append("Review test structure and code quality signals before release.")
    if not recommendations:
        recommendations.append("The page is simple; extend with richer user flows as the product grows.")
    return recommendations


def _summary(run: dict) -> dict:
    ui_input = run.get("ui_input", {})
    llm_interpretation = run.get("llm_interpretation", {})
    ui_discovery = run.get("ui_discovery", {})
    ui_execution = run.get("ui_execution", {})
    generated_pom = run.get("generated_pom", {})
    inspection_report = run.get("inspection_report", {})
    cognitive_debt_report = run.get("cognitive_debt_report", {})
    audit_trail = run.get("audit_trail", [])
    workflow = _workflow_overview(run)
    history = _history()
    history_summary = _history_summary(history)

    return {
        "url": ui_input.get("url", "-"),
        "problem_statement": ui_input.get("problem_statement", "-"),
        "problem_summary": _short_text(ui_input.get("problem_statement", "-"), 180),
        "scenario_title": _humanize(ui_input.get("scenario_name", "scenario")),
        "scenario_name": ui_input.get("scenario_name", "-"),
        "target_platform": ui_input.get("target_platform", "-"),
        "execution_mode": ui_execution.get("execution_mode", ui_input.get("execution_mode", "auto")),
        "run_profile": ui_input.get("run_profile", "demo"),
        "intent_type": llm_interpretation.get("intent_type", "unknown"),
        "release_readiness_score": _latest_value(run, "release_readiness_score", 0),
        "human_decision": _latest_value(run, "human_decision", "pending"),
        "audit_trail_count": len(audit_trail),
        "discovered_elements_count": len(ui_discovery.get("elements", [])),
        "execution_passed": ui_execution.get("passed", False),
        "executed_steps_count": len(ui_execution.get("steps", [])),
        "locator_summary": ui_execution.get("locator_summary", {}),
        "generated_class_name": generated_pom.get("class_name", "-"),
        "defect_count": inspection_report.get("defect_count", 0),
        "flake_count": inspection_report.get("flake_count", 0),
        "environment_issue_count": inspection_report.get("environment_issue_count", 0),
        "debt_score": cognitive_debt_report.get("debt_score", 0),
        "debt_severity": cognitive_debt_report.get("severity", "LOW"),
        "artifact_root": ui_discovery.get("artifact_root", "-"),
        "risk_zones": run.get("risk_zones", {}),
        "skill_plan": run.get("skill_plan", []),
        "capability_groups": _capability_groups(run),
        "workflow": workflow,
        "history": history_summary,
    }


def _history_summary(history: list[dict]) -> dict:
    recent = history[-5:]
    scores = [entry.get("score", 0) for entry in recent]
    latest = recent[-1] if recent else {}
    previous = recent[-2] if len(recent) > 1 else {}
    delta = latest.get("score", 0) - previous.get("score", 0) if previous else 0
    return {
        "run_count": len(history),
        "recent_scores": scores,
        "score_delta": delta,
        "latest_scenario": latest.get("scenario_name", ""),
        "latest_intent": latest.get("intent_type", "unknown"),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    run = _latest_run()
    template = templates.get_template("index.html")
    html = template.render(
        request=request,
        summary=_summary(run),
        capabilities=_capability_groups(run),
        agent_cards=_agent_cards(run),
        recommendations=_recommendations(run),
    )
    return HTMLResponse(html)


@app.get("/audit", response_class=HTMLResponse)
def audit_trail_page(request: Request):
    run = _latest_run()
    template = templates.get_template("index.html")
    html = template.render(
        request=request,
        summary=_summary(run),
        capabilities=_capability_groups(run),
        agent_cards=_agent_cards(run),
        recommendations=_recommendations(run),
        focus_section="audit",
    )
    return HTMLResponse(html)


@app.get("/api/snapshot")
def snapshot():
    run = _latest_run()
    return JSONResponse({
        "summary": _summary(run),
        "capabilities": _capability_groups(run),
        "agent_cards": _agent_cards(run),
        "recommendations": _recommendations(run),
        "workflow_overview": _workflow_overview(run),
    })
