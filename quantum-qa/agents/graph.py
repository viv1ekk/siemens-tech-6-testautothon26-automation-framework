"""
QUANTUM-QA Lean Execution Pipeline
Runs only UI Pipeline + Bug Reporter for Playwright test results
"""

import time

from agents.ui_pipeline import UiPipelineAgent
from agents.bug_reporter import BugReporterAgent
from agents.dashboard_agent import DashboardAgent


class SimplePipeline:
    def __init__(self):
        self.ui_pipeline = UiPipelineAgent()
        self.bug_reporter = BugReporterAgent()
        self.dashboard_agent = DashboardAgent()

    def invoke(self, state: dict, config=None) -> dict:
        state = dict(state)
        state.setdefault("audit_trail", [])
        state.setdefault("ui_input", {})
        state.setdefault("llm_interpretation", {})
        state.setdefault("ui_discovery", {})
        state.setdefault("ui_workflow", {})
        state.setdefault("generated_pom", {})
        state.setdefault("ui_test_layer", {})
        state.setdefault("ui_execution", {})
        state.setdefault("bug_report", {})

        # Run UI Pipeline (Playwright test)
        ui_pipeline_out = self.ui_pipeline.run(state.get("ui_input", {}))
        state["ui_input"] = ui_pipeline_out["ui_input"]
        state["llm_interpretation"] = ui_pipeline_out.get("llm_interpretation", {})
        state["ui_discovery"] = ui_pipeline_out["ui_discovery"]
        state["ui_workflow"] = ui_pipeline_out["ui_workflow"]
        state["generated_pom"] = ui_pipeline_out["generated_pom"]
        state["ui_test_layer"] = ui_pipeline_out["ui_test_layer"]
        state["ui_execution"] = ui_pipeline_out["ui_execution"]
        state["audit_trail"].append(ui_pipeline_out["audit_entry"])

        # Generate bug report from failures
        bug_report = self.bug_reporter.report(state.get("ui_execution", {}))
        state["bug_report"] = bug_report
        state["audit_trail"].append(bug_report["audit_entry"])

        # Build post-run executive dashboard HTML from collected artifacts.
        dashboard_report = self.dashboard_agent.report(state)
        state["dashboard_report"] = dashboard_report.get("dashboard", {})
        state["audit_trail"].append(dashboard_report.get("audit_entry", {}))

        return state


def build_graph(checkpointer=None):
    return SimplePipeline()
