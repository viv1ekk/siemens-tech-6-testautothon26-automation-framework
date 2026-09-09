"""
QUANTUM-QA URL Flow Runner
Standalone entrypoint for URL/problem-statement driven UI discovery and
artifact generation without the demo harness.
"""

import argparse
import csv
import datetime
import json
import os
import time
import zipfile
from pathlib import Path

from agents.graph import build_graph
from llm.client import SiemensLLMClient
from reports.execution_report_builder import build_execution_report


def _set_project_root_as_cwd() -> None:
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)


def run_url_flow(ui_input: dict) -> dict:
    graph = build_graph()
    state = {
        "commit_sha": f"ui_{int(time.time())}",
        "commit_diff": "",
        "risk_zones": {},
        "skill_plan": [],
        "execution_results": {},
        "inspection_report": {"failures": [], "defect_count": 0, "flake_count": 0, "stable_count": 0},
        "cognitive_debt_report": {"debt_score": 0, "severity": "LOW", "issues": []},
        "learned_patterns": {},
        "audit_trail": [],
        "release_readiness_score": 100,
        "human_decision": None,
        "platforms": [ui_input.get("target_platform", "web")],
        "iteration": 0,
        "ui_input": ui_input,
    }
    final_state = graph.invoke(state, {"configurable": {"thread_id": state["commit_sha"]}})
    _persist_run_artifacts(final_state)
    return final_state


def _persist_run_artifacts(final_state: dict) -> None:
    dashboard_data_dir = Path("dashboard") / "data"
    dashboard_data_dir.mkdir(parents=True, exist_ok=True)
    (dashboard_data_dir / "latest_run.json").write_text(json.dumps(final_state, indent=2))
    (dashboard_data_dir / "audit_trail.json").write_text(json.dumps(final_state.get("audit_trail", []), indent=2))
    _generate_hackathon_artifacts(final_state)


def _generate_hackathon_artifacts(final_state: dict) -> None:
    root = Path("artifacts")
    testcases_dir = root / "testcases"
    execution_dir = root / "execution"
    reports_dir = root / "reports"
    bugs_dir = root / "bugs"
    for directory in [testcases_dir, execution_dir, reports_dir, bugs_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    generated_testcases = _build_generated_testcases(final_state)
    (testcases_dir / "generated_testcases.json").write_text(json.dumps(generated_testcases, indent=2), encoding="utf-8")

    execution_plan = _build_execution_plan(final_state)
    (execution_dir / "core_action_plan.json").write_text(json.dumps(execution_plan, indent=2), encoding="utf-8")
    ui_execution = final_state.get("ui_execution", {})
    (execution_dir / "execution_result.json").write_text(json.dumps(ui_execution, indent=2), encoding="utf-8")
    try:
        build_execution_report(ui_execution, Path(ui_execution.get("artifact_root", "artifacts")))
    except Exception:
        pass

    bugs = _build_bug_reports(final_state)
    (bugs_dir / "bug_report.json").write_text(json.dumps({"bugs": bugs}, indent=2), encoding="utf-8")
    _write_bug_report_csv(bugs_dir / "bug_report.csv", bugs)
    _write_bug_report_xlsx(bugs_dir / "bug_report.xlsx", bugs)

    business_impact_md = _build_business_impact_markdown(final_state, bugs)
    business_impact_path = reports_dir / "business_impact_analysis.md"
    business_impact_path.write_text(business_impact_md, encoding="utf-8")

    test_summary_md = _build_test_summary_markdown(final_state, bugs)
    test_summary_path = reports_dir / "test_summary.md"
    test_summary_path.write_text(test_summary_md, encoding="utf-8")

    _write_simple_pdf(reports_dir / "business_impact_analysis.pdf", business_impact_md)
    _write_simple_pdf(reports_dir / "test_summary.pdf", test_summary_md)
    _write_simple_pdf(bugs_dir / "bug_report.pdf", _bugs_to_plain_text(bugs))


def _build_generated_testcases(final_state: dict) -> dict:
    llm_interpretation = final_state.get("llm_interpretation", {})
    workflow = final_state.get("ui_workflow", {})
    actions = workflow.get("actions", [])
    llm_steps = llm_interpretation.get("steps", [])
    case_steps = []

    if isinstance(llm_steps, list) and llm_steps:
        for index, step in enumerate(llm_steps, start=1):
            case_steps.append(
                {
                    "step_no": index,
                    "action": str(step.get("action", "unknown")),
                    "target": str(step.get("target", "")),
                    "value": str(step.get("value", "")) if step.get("value") is not None else "",
                    "source": "llm",
                }
            )
    else:
        for index, action in enumerate(actions, start=1):
            case_steps.append(
                {
                    "step_no": index,
                    "action": str(action.get("kind", "unknown")),
                    "target": str(action.get("target", "")),
                    "value": str(action.get("value", "")) if action.get("value") is not None else "",
                    "source": "workflow",
                }
            )

    return {
        "generated_at": _now_iso(),
        "scenario_name": final_state.get("ui_input", {}).get("scenario_name", "ui-smoke"),
        "url": final_state.get("ui_input", {}).get("url", ""),
        "intent_type": llm_interpretation.get("intent_type", "unknown"),
        "test_cases": [
            {
                "id": "TC-001",
                "title": f"Autogenerated {llm_interpretation.get('intent_type', 'workflow')} workflow",
                "preconditions": ["Target URL reachable"],
                "steps": case_steps,
                "expected_result": "Workflow completes with expected assertions.",
            }
        ],
    }


def _generate_negative_testcases(problem_statement: str) -> list[dict]:
    """Call the Siemens LLM client to author 2 negative test cases per use case identified in the problem statement."""
    problem_statement = str(problem_statement or "").strip()
    if not problem_statement:
        return []

    try:
        llm_client = SiemensLLMClient()
    except ValueError:
        return []

    prompt = _build_negative_testcase_prompt(problem_statement)
    try:
        raw_response = llm_client.generate_llm_content(prompt)
    except Exception:
        return []

    negative_cases: list[dict] = []
    for counter, parsed_case in enumerate(_parse_negative_testcase_response(raw_response), start=1):
        negative_cases.append(
            {
                "id": f"NEC-TC-{counter:03d}",
                "title": parsed_case.get("title", ""),
                "description": parsed_case.get("description", ""),
                "expected_result": parsed_case.get("expected_result", ""),
            }
        )
    return negative_cases


def _build_negative_testcase_prompt(problem_statement: str) -> str:
    return (
        "Identify up to 5 distinct use cases described in the following problem statement, "
        "then generate exactly 2 negative test cases for each identified use case.\n\n"
        f"Problem Statement: {problem_statement}\n\n"
        "Format strictly as a semicolon-separated list: "
        "Title=<title>,Description=<description>,Expected Result=<expected result>; "
        "Title=<title>,Description=<description>,Expected Result=<expected result>; ..."
    )


def _parse_negative_testcase_response(raw_response: str) -> list[dict]:
    cases = []
    for chunk in str(raw_response).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = {}
        for part in chunk.split(","):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            fields[key.strip().lower()] = value.strip()
        title = fields.get("title", "")
        description = fields.get("description", "")
        expected_result = fields.get("expected result", "")
        if title or description or expected_result:
            cases.append({"title": title, "description": description, "expected_result": expected_result})
    return cases


def _write_negative_testcases_markdown(path: Path, negative_cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Negative Test Cases", "", f"- Generated at: {_now_iso()}", ""]
    if not negative_cases:
        lines.append("No negative test cases generated for this run.")
    else:
        for case in negative_cases:
            lines.extend(
                [
                    f"## {case['id']}",
                    f"- Title: {case.get('title', '')}",
                    f"- Description: {case.get('description', '')}",
                    f"- Expected Result: {case.get('expected_result', '')}",
                    "",
                ]
            )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _build_execution_plan(final_state: dict) -> dict:
    workflow = final_state.get("ui_workflow", {})
    execution = final_state.get("ui_execution", {})
    actions = workflow.get("actions", [])
    return {
        "generated_at": _now_iso(),
        "execution_mode": workflow.get("execution_mode", "auto"),
        "check_summary": execution.get("check_summary", {}),
        "steps": [
            {
                "stage": action.get("stage", index + 1),
                "action": action.get("kind", "unknown"),
                "target": action.get("target", ""),
                "value": action.get("value", ""),
                "required": bool(action.get("required", False)),
                "raw_step": action.get("raw_step", ""),
            }
            for index, action in enumerate(actions)
        ],
    }


def _build_bug_reports(final_state: dict) -> list[dict]:
    report = final_state.get("inspection_report", {})
    failures = report.get("failures", [])
    bugs = []
    for index, failure in enumerate(failures, start=1):
        classification = str(failure.get("classification", "PRODUCT_DEFECT"))
        severity = str(failure.get("severity", "medium")).upper()
        title = f"{classification} in {failure.get('skill', 'workflow')}"
        description = str(failure.get("error", "Failure detected during run"))
        recommendation = str(failure.get("recommendation", "Investigate and fix the root cause."))
        root_cause = str(failure.get("root_cause", "Undetermined"))
        bugs.append(
            {
                "id": f"BUG-{index:03d}",
                "title": title,
                "description": description,
                "severity": severity,
                "platform": str(failure.get("platform", "web")),
                "repo_steps": f"Run ui_flow.py for scenario and reproduce step: {failure.get('skill', 'workflow')}",
                "classification": classification,
                "root_cause": root_cause,
                "recommendation": recommendation,
                "confidence": failure.get("confidence", 0.0),
            }
        )
    return bugs


def _write_bug_report_csv(path: Path, bugs: list[dict]) -> None:
    headers = ["id", "title", "description", "severity", "platform", "repo_steps", "classification", "root_cause", "recommendation", "confidence"]
    with path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        writer.writeheader()
        for bug in bugs:
            writer.writerow({header: bug.get(header, "") for header in headers})


def _write_bug_report_xlsx(path: Path, bugs: list[dict]) -> None:
    headers = ["id", "title", "description", "severity", "platform", "repo_steps", "classification", "root_cause", "recommendation", "confidence"]
    rows = [headers] + [[str(bug.get(header, "")) for header in headers] for bug in bugs]
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cell_ref = f"{_xlsx_col_name(col_index)}{row_index}"
            escaped = _xml_escape(value)
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{escaped}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="BugReport" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<fonts count=\"1\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>"
        "<fills count=\"1\"><fill><patternFill patternType=\"none\"/></fill></fills>"
        "<borders count=\"1\"><border/></borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types_xml)
        xlsx.writestr("_rels/.rels", root_rels_xml)
        xlsx.writestr("xl/workbook.xml", workbook_xml)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        xlsx.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        xlsx.writestr("xl/styles.xml", styles_xml)


def _build_business_impact_markdown(final_state: dict, bugs: list[dict]) -> str:
    score = final_state.get("release_readiness_score", 0)
    lines = [
        "# Business Impact Analysis",
        "",
        f"- Generated at: {_now_iso()}",
        f"- Release readiness score: {score}/100",
        f"- Total reported bugs: {len(bugs)}",
        "",
        "## Impact Summary",
    ]
    if not bugs:
        lines.append("- No current defects detected. Business risk is low.")
    else:
        for bug in bugs:
            lines.extend(
                [
                    f"### {bug['id']} - {bug['title']}",
                    f"- Severity: {bug['severity']}",
                    f"- Platform: {bug['platform']}",
                    f"- Customer Impact: {bug['description']}",
                    f"- Business Risk: {bug['root_cause']}",
                    f"- Recommendation: {bug['recommendation']}",
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def _build_test_summary_markdown(final_state: dict, bugs: list[dict]) -> str:
    ui_execution = final_state.get("ui_execution", {})
    workflow_summary = ui_execution.get("workflow_summary", {})
    check_summary = ui_execution.get("check_summary", {})
    categories = check_summary.get("categories", {})
    total_checks = int(check_summary.get("total_checks", 0) or 0)
    ui_input = final_state.get("ui_input", {})
    lines = [
        "# TS Report - Test Summary",
        "",
        f"- Generated at: {_now_iso()}",
        f"- URL: {ui_input.get('url', '')}",
        f"- Scenario: {ui_input.get('scenario_name', 'ui-smoke')}",
        f"- Intent: {final_state.get('llm_interpretation', {}).get('intent_type', 'unknown')}",
        f"- Steps Passed: {workflow_summary.get('passed', 0)}",
        f"- Steps Planned: {workflow_summary.get('planned', 0)}",
        f"- Release Readiness: {final_state.get('release_readiness_score', 0)}/100",
        f"- Human Decision: {final_state.get('human_decision', 'pending')}",
        "",
        "## Bug Snapshot",
        f"- Total Bugs: {len(bugs)}",
    ]
    if total_checks > 0:
        lines.extend(
            [
                f"- Non-functional Checks Passed: {check_summary.get('passed_checks', 0)}",
                f"- Non-functional Checks Failed: {check_summary.get('failed_checks', 0)}",
            ]
        )
    if categories:
        lines.extend(["", "## Non-functional Categories"])
        for category_name in ["accessibility", "performance", "security", "visual"]:
            category = categories.get(category_name)
            if not category:
                continue
            lines.append(
                f"- {category_name.title()}: {category.get('passed', 0)} passed, {category.get('failed', 0)} failed, {category.get('issues', 0)} issues"
            )
    for bug in bugs[:10]:
        lines.append(f"- {bug['id']} | {bug['severity']} | {bug['title']}")
    if not bugs:
        lines.append("- No bugs detected in this run.")
    return "\n".join(lines).strip() + "\n"


def _write_simple_pdf(path: Path, text: str) -> None:
    lines = [line[:110] for line in str(text).replace("\r\n", "\n").split("\n")]
    pages = []
    page_size = 45
    for start in range(0, max(len(lines), 1), page_size):
        chunk = lines[start:start + page_size] or [""]
        commands = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for line in chunk:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        pages.append("\n".join(commands))

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("ascii"))

    for i, content in enumerate(pages):
        page_obj_num = 3 + i * 2
        content_obj_num = page_obj_num + 1
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> /Contents {content_obj_num} 0 R >>"
        )
        content_bytes = content.encode("latin-1", "replace")
        content_obj = f"<< /Length {len(content_bytes)} >>\nstream\n".encode("ascii") + content_bytes + b"\nendstream"
        objects.append(page_obj.encode("ascii"))
        objects.append(content_obj)

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = bytearray()
    pdf.extend(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(pdf))


def _bugs_to_plain_text(bugs: list[dict]) -> str:
    lines = ["Bug Report"]
    for bug in bugs:
        lines.extend(
            [
                "",
                f"{bug['id']}: {bug['title']}",
                f"Severity: {bug['severity']}",
                f"Platform: {bug['platform']}",
                f"Description: {bug['description']}",
                f"Repro Steps: {bug['repo_steps']}",
            ]
        )
    if len(lines) == 1:
        lines.append("No bugs reported in this run.")
    return "\n".join(lines)


def _pdf_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _xlsx_col_name(index: int) -> str:
    name = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xml_escape(value: str) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _clean_previous_run_data() -> None:
    targets = [
        Path("artifacts") / "ui_generated",
        Path("artifacts") / "testcases",
        Path("artifacts") / "execution",
        Path("artifacts") / "reports",
        Path("artifacts") / "bugs",
        Path("dashboard") / "data" / "latest_run.json",
        Path("dashboard") / "data" / "audit_trail.json",
    ]
    for target in targets:
        if target.exists():
            if target.is_dir():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                target.rmdir()
            else:
                target.unlink()


def main():
    _set_project_root_as_cwd()
    parser = argparse.ArgumentParser(description="QUANTUM-QA URL Flow Runner")
    parser.add_argument("--ui-url", required=True)
    parser.add_argument("--ui-problem-statement", default="")
    parser.add_argument("--ui-problem-statement-file", default="")
    parser.add_argument("--ui-platform", default="web")
    parser.add_argument("--ui-scenario", default="ui-smoke")
    parser.add_argument("--ui-mode", default="auto", choices=["auto", "strict", "explore"])
    parser.add_argument("--run-profile", default="demo", choices=["demo", "balanced", "thorough"])
    parser.add_argument("--output", default="summary", choices=["summary", "json"])
    parser.add_argument("--step-pause-ms", type=int, default=1200)
    parser.add_argument("--keep-browser-open-ms", type=int, default=2500)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--nfr-only", action="store_true")
    parser.add_argument("--clean-run-data", action="store_true")
    args = parser.parse_args()
    if args.clean_run_data:
        _clean_previous_run_data()
    try:
        problem_statement = _resolve_problem_statement(args.ui_problem_statement, args.ui_problem_statement_file)
    except ValueError as exc:
        parser.error(str(exc))

    ui_input = {
        "url": args.ui_url,
        "problem_statement": problem_statement,
        "target_platform": args.ui_platform,
        "scenario_name": args.ui_scenario,
        "execution_mode": args.ui_mode,
        "run_profile": args.run_profile,
        "step_pause_ms": args.step_pause_ms,
        "keep_browser_open_ms": args.keep_browser_open_ms,
        "headed": args.headed,
        "nfr_only": args.nfr_only,
    }

    negative_test_cases = _generate_negative_testcases(problem_statement)
    negative_testcases_path = Path("artifacts") / "testcases" / "negative_testcases.md"
    _write_negative_testcases_markdown(negative_testcases_path, negative_test_cases)

    final_state = run_url_flow(ui_input)
    summary = {
        "llm_interpretation": final_state.get("llm_interpretation", {}),
        "ui_discovery": final_state.get("ui_discovery", {}),
        "ui_workflow": final_state.get("ui_workflow", {}),
        "generated_pom": final_state.get("generated_pom", {}),
        "ui_test_layer": final_state.get("ui_test_layer", {}),
        "ui_execution": final_state.get("ui_execution", {}),
        "dashboard_report": final_state.get("dashboard_report", {}),
        "release_readiness_score": final_state.get("release_readiness_score"),
        "human_decision": final_state.get("human_decision"),
        "audit_trail_count": len(final_state.get("audit_trail", [])),
    }
    if args.output == "json":
        print(json.dumps(summary, indent=2))
    else:
        print(_terminal_summary(summary))


def _resolve_problem_statement(inline_statement: str, file_path: str) -> str:
    inline_value = str(inline_statement or "").strip()
    file_value = str(file_path or "").strip()
    if inline_value and file_value:
        raise ValueError("Use either --ui-problem-statement or --ui-problem-statement-file, not both.")
    if file_value:
        path = _resolve_problem_file_path(file_value)
        if path is None:
            raise ValueError(
                "Problem statement file not found. "
                f"Tried: {file_value}, {Path(__file__).resolve().parent / file_value}, {Path(__file__).resolve().parent.parent / file_value}"
            )
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"Problem statement file is empty: {path}")
        return content
    if inline_value:
        return inline_value
    raise ValueError("Provide --ui-problem-statement or --ui-problem-statement-file.")


def _resolve_problem_file_path(file_value: str) -> Path | None:
    requested = Path(file_value)
    if requested.is_absolute() and requested.exists() and requested.is_file():
        return requested
    base_dirs = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]
    for base in base_dirs:
        candidate = (base / requested).resolve()
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _terminal_summary(summary: dict) -> str:
    interpretation = summary.get("llm_interpretation", {})
    workflow = summary.get("ui_workflow", {})
    execution = summary.get("ui_execution", {})
    workflow_summary = execution.get("workflow_summary", {})
    check_summary = execution.get("check_summary", {})
    categories = check_summary.get("categories", {})
    total_checks = int(check_summary.get("total_checks", 0) or 0)
    lines = [
        "QUANTUM-QA RUN SUMMARY",
        f"Intent: {interpretation.get('intent_type', workflow.get('workflow_type', 'unknown'))}",
        f"Mode: {workflow.get('execution_mode', 'auto')}",
        f"Workflow: {workflow_summary.get('passed', 0)}/{workflow_summary.get('planned', 0)} steps passed",
        f"Release readiness: {summary.get('release_readiness_score', 0)}/100",
        f"Human decision: {summary.get('human_decision', 'pending')}",
        f"Artifacts: {summary.get('ui_discovery', {}).get('artifact_root', '-')}",
    ]
    if total_checks > 0:
        lines.insert(
            4,
            f"Non-functional checks: {check_summary.get('passed_checks', 0)}/{check_summary.get('total_checks', 0)} passed",
        )
    if categories:
        ordered = []
        for name in ["accessibility", "performance", "security", "visual"]:
            data = categories.get(name)
            if data:
                ordered.append((name, data))
        if ordered:
            lines.append(
                "Checks: " + ", ".join(
                    f"{name} {data.get('passed', 0)}/{data.get('passed', 0) + data.get('failed', 0)}"
                    for name, data in ordered
                )
            )
    dashboard_path = str(summary.get("dashboard_report", {}).get("path", "")).strip()
    if dashboard_path:
        lines.append(f"Dashboard: {dashboard_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()




