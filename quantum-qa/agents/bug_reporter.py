"""
Simple Bug Reporter Agent - Generates bug reports from test failures
"""

class BugReporterAgent:
    def report(self, ui_execution: dict) -> dict:
        """Extract failures from execution results and generate bug report"""
        bugs = []
        steps = ui_execution.get("steps", [])
        
        for step in steps:
            if step.get("status") == "failed":
                bug = {
                    "id": f"BUG-{len(bugs) + 1}",
                    "step": step.get("step", ""),
                    "raw_step": step.get("raw_step", ""),
                    "error": step.get("error", "Unknown error"),
                    "stage": step.get("stage"),
                    "screenshot": step.get("details", {}).get("screenshot", ""),
                    "severity": "HIGH" if "locator" in step.get("error", "").lower() else "MEDIUM",
                }
                bugs.append(bug)
        
        return {
            "bugs": bugs,
            "total_failures": len(bugs),
            "audit_entry": {
                "agent": "BugReporter",
                "bugs_reported": len(bugs),
                "timestamp": __import__("time").strftime("%H:%M:%S")
            }
        }
