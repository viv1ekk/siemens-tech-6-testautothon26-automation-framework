# QUANTUM-QA: Gajab E-Commerce Automation

Autonomous UI workflow execution using Playwright, with business-oriented reporting.

## What This Framework Does

- Discovers interactive UI elements from a URL.
- Converts plain-English problem statements into executable actions.
- Executes workflows in a real browser (headed or headless).
- Captures step-wise evidence and execution JSON.
- Generates business-ready reports (JSON, CSV, XLSX, PDF, Markdown).
- Supports both functional and non-functional validation (NFR).

## Repository Layout

```text
quantum-qa/
   ui_flow.py                        # CLI entrypoint
   agents/
      graph.py                        # Pipeline orchestration
      ui_pipeline.py                  # Discovery + workflow build + execution
      bug_reporter.py                 # Failure-to-bug classification
   Problemstatement/
      workflow_gajab.txt              # Main end-to-end functional scenario
      workflow_gajab_negative_business.txt  # Business-impacting negative scenarios
   specs/
      auth.spec.yaml
      payment.spec.yaml

artifacts/                          # Generated run outputs (gitignored)
dashboard/data/                     # Latest run snapshots for dashboard
```

## Setup

From repo root:

```bash
pip install -r quantum-qa/requirements.txt
playwright install chromium
```

On Windows, you can use `py` instead of `python`.

## Run Functional Workflow

```bash
py quantum-qa/ui_flow.py \
   --ui-url "https://stg.gajab.com/" \
   --ui-problem-statement-file "quantum-qa/Problemstatement/workflow_gajab.txt" \
   --headed
```

Useful runtime flags:

- `--clean-run-data`: clears prior generated run data.
- `--step-pause-ms 1200`: controls pace between steps.
- `--keep-browser-open-ms 2500`: keeps browser visible briefly after completion.
- `--output summary|json`: terminal output format.

## Run Non-Functional (NFR) Checks

NFR checks currently supported by the workflow engine:

- Accessibility scan
- Performance budget
- Security smoke checks
- Visual comparison (baseline hash)

Run only NFR checks:

```bash
py quantum-qa/ui_flow.py \
   --ui-url "https://stg.gajab.com/" \
   --ui-problem-statement-file "quantum-qa/Problemstatement/workflow_gajab.txt" \
   --nfr-only \
   --headed
```

Run profile tuning:

- `--run-profile demo`: most tolerant, optimized for live demo stability.
- `--run-profile balanced`: moderate strictness.
- `--run-profile thorough`: strictest thresholds.

Note: NFR summary sections are shown only when NFR checks actually execute.

## Run Business-Impacting Negative Tests

Negative scenario pack file:

- `quantum-qa/Problemstatement/workflow_gajab_negative_business.txt`

Run command:

```bash
py quantum-qa/ui_flow.py \
   --ui-url "https://stg.gajab.com/" \
   --ui-problem-statement-file "quantum-qa/Problemstatement/workflow_gajab_negative_business.txt" \
   --headed
```

Included negative scenarios:

1. Request OTP without accepting Terms and Conditions (compliance guard).
2. Invalid OTP rejection (account takeover/fraud prevention).
3. Non-serviceable pin code rejection (fulfillment risk control).

Authoring guideline for problem statement files:

- Keep executable steps as numbered actions.
- Avoid free-text lines between steps unless they are prefixed as comments or kept outside the scenario body.

## Post-Run Hackathon Dashboard

After each run, a static executive dashboard is generated automatically by `DashboardAgent`.

Generated file:

- `artifacts/reports/hackathon_dashboard.html`

The dashboard includes:

- Test coverage and pass rate
- Passed/failed counts
- Failed steps with screenshot evidence
- Human gate decision and release readiness
- Defects observed by automation, including severity and screenshots
- NFR outcome summary (only when NFR checks run)

Open it directly in a browser after the run.

## Outputs and Reports

Primary generated outputs:

- `artifacts/ui_generated/.../ui_execution.json`
- `artifacts/execution/core_action_plan.json`
- `artifacts/execution/execution_result.json`
- `artifacts/reports/test_summary.md`
- `artifacts/reports/business_impact_analysis.md`
- `artifacts/bugs/bug_report.json`
- `artifacts/bugs/bug_report.csv`
- `artifacts/bugs/bug_report.xlsx`
- `artifacts/bugs/bug_report.pdf`

Dashboard snapshots:

- `dashboard/data/latest_run.json`
- `dashboard/data/audit_trail.json`

## Architecture Reference

See `ARCHITECTURE.md` for detailed architecture, data flow, and extension points.

## Troubleshooting

- Browser does not appear:
   - add `--headed`.
- Slow environment causes flaky steps:
   - increase `--step-pause-ms` and/or use `--run-profile demo` for NFR runs.
- Need a clean result set:
   - run with `--clean-run-data`.

## Notes on Git

- Generated `artifacts/` content is ignored via `.gitignore`.
- If files were previously tracked, run once:

```bash
git rm -r --cached artifacts
```
