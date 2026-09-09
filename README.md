# QUANTUM-QA: Gajab E-Commerce Automation

**TestAthon Bangalore 2026** — Autonomous Testing with Agentic AI

> Automated end-to-end Gajab e-commerce workflow with Playwright + intelligent bug reporting

---

## What This Does

QUANTUM-QA is a **Playwright-based UI automation framework** for TestAthon that:

✅ **Discovers** UI elements from any website using Playwright  
✅ **Parses** natural language problem statements into executable workflow steps  
✅ **Executes** multi-page workflows (navigate → click → fill → assert)  
✅ **Captures** failures as bug reports (JSON, CSV, XLSX, PDF)  
✅ **Generates** screenshots for every step  
✅ **Fast**: All timeouts capped at 5-8 seconds (demo-ready performance)

---

## Quick Start

### 1. Clone & Install

`ash
git clone <repo>
cd vivekkumar-sagcp-bookish-fortnight

pip install -r requirements.txt
playwright install chromium
`

### 2. Run the Gajab Workflow

`ash
python quantum-qa/ui_flow.py \
  --ui-url "https://stg.gajab.com/" \
  --ui-problem-statement-file "quantum-qa/Problemstatement/workflow_gajab.txt" \
  --clean-run-data \
  --headed
`

**Options:**
- --headed — Show browser during execution
- --clean-run-data — Clear previous run artifacts
- --step-pause-ms 1000 — Slow down for demo visibility

### 3. View Bug Reports

After execution, check:

`
artifacts/bugs/
├── bug_report.json     # Detailed bug data
├── bug_report.csv      # Spreadsheet format
├── bug_report.xlsx     # Excel format
└── bug_report.pdf      # PDF report
`

---

## The Gajab Test Case (60 Steps)

**Workflow:** quantum-qa/Problemstatement/workflow_gajab.txt

**Scenario:** Complete e-commerce customer journey:

1. **Login** (Steps 1-15)
   - Navigate → Sign in → Enter mobile → Request OTP
   - Verify OTP → Create profile (name, gender)
   
2. **Location Setup** (Steps 16-21)
   - Select location via pin code
   - Verify location applied

3. **Deal Discovery** (Steps 22-25)
   - View Deal of the Day
   - Email product details

4. **Market Research** (Steps 26-35)
   - Browse Trending Products
   - Filter by category (Toys & Games)
   - Apply brand filter (SERA'S BASKET)
   - Apply price range (427-727)

5. **Bargaining** (Steps 36-48)
   - Select product
   - Initiate bargain
   - Submit 3 counter-offers
   - Accept final offer

6. **Payment** (Steps 49-52)
   - Click Buy Now
   - Select Pay Online
   - Choose Net Banking
   - Confirm payment

7. **Verification** (Steps 53-58)
   - Verify order placed
   - Check My Bargains
   - Validate savings

8. **Logout** (Steps 59-60)
   - Sign out & confirm

---

## Architecture

`
Problem Statement (workflow_gajab.txt)
         ↓
ui_flow.py (Entry Point)
         ↓
UiPipelineAgent (Playwright)
  ├─ Interpret workflow steps
  ├─ Discover page elements
  ├─ Build action sequence
  ├─ Execute in Playwright browser
  └─ Capture screenshots & results
         ↓
BugReporterAgent (Report Generation)
  ├─ Extract failures
  ├─ Classify as bugs
  └─ Generate reports (JSON/CSV/XLSX/PDF)
         ↓
artifacts/bugs/ (Output)
`

---

## Key Features

### ⚡ Fast Execution
- All timeouts: **5-8 seconds max**
- Element not found: **Fail in 5 seconds** (not 30s)
- Perfect for demo scenarios

### 🔍 Intelligent Element Discovery
- Role-based lookup (get_by_role)
- Text-based lookup (get_by_text)
- Attribute matching (CSS selectors)
- Context-aware form field detection

### 📸 Evidence Capture
- Screenshot on every step
- Step status tracking (pass/fail/skip)
- URL & page title logging
- Failure details with error messages

### 📊 Bug Reporting
- **JSON**: Full structured bug data
- **CSV**: Spreadsheet-compatible format
- **XLSX**: Excel workbook with formatting
- **PDF**: Human-readable report

### 🎯 Supported Actions
- goto → Navigate to URL
- click → Click elements
- ill → Enter text
- check → Toggle checkboxes
- select → Choose dropdown options
- wait → Wait for page/element
- efresh → Reload page
- ssert_visible → Text assertions
- ssert_url_contains → URL assertions

---

## Project Structure

`
quantum-qa/
├── agents/
│   ├── ui_pipeline.py       # Playwright orchestration (2084 lines)
│   ├── bug_reporter.py      # Bug extraction & reporting
│   └── graph.py             # Pipeline execution
├── Problemstatement/
│   └── workflow_gajab.txt   # 60-step Gajab workflow
├── platforms/               # Web execution helpers
├── ui_flow.py               # Main entrypoint
└── requirements.txt         # Dependencies

artifacts/
└── bugs/                    # Bug reports (generated)
    ├── bug_report.json
    ├── bug_report.csv
    ├── bug_report.xlsx
    └── bug_report.pdf
`

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **Orchestration** | Python 3.10+ |
| **Browser Automation** | Playwright |
| **UI Discovery** | Playwright locators |
| **Workflow Language** | Plain English (txt) |
| **Reporting** | JSON, CSV, XLSX, PDF |

---

## Performance Tuning

### Timeout Strategy
- **Initial page load**: 8 seconds
- **Element lookup**: 5 seconds
- **Assertions**: 5s → 3s → 2s (retry levels)
- **Step pause**: 500ms (demo visibility)

### Why Fast?
- Demo scenarios need quick feedback
- Page should stabilize in 8s or fail
- No waiting for slow network
- Timeouts tuned for stg.gajab.com

---

## Example Execution Output

`
[PLAYWRIGHT TEST COMPLETE]
Steps: 60 total | 45 passed | 10 failed | 5 skipped
Bugs Found: 10
Bug Report: artifacts/bugs/bug_report.json
`

### Bug Report Sample (JSON)
`json
{
  "bugs": [
    {
      "id": "BUG-1",
      "step": "click:Male",
      "raw_step": "Click on 'Male' button for gender selection",
      "error": "Target not found",
      "stage": 11,
      "severity": "HIGH"
    }
  ]
}
`

---

## Running in Demo Mode

**Best for presentations:**

`ash
python quantum-qa/ui_flow.py \
  --ui-url "https://stg.gajab.com/" \
  --ui-problem-statement-file "quantum-qa/Problemstatement/workflow_gajab.txt" \
  --headed \
  --step-pause-ms 1500 \
  --clean-run-data
`

Browser will display each step with 1.5s pause — perfect for live demos!

---

## Troubleshooting

### Playwright not installed
`ash
playwright install chromium
`

### Timeouts on slow pages
Edit quantum-qa/agents/ui_pipeline.py and increase timeout values (currently 5-8s)

### Element not found in bug report
Check rtifacts/bugs/bug_report.json for step details and screenshots

### Browser window not opening
Remove --headed flag or add --keep-browser-open-ms 5000

---

## GitHub Submission

**For TestAthon 2026:**
- ✅ Lean, focused codebase (no extra agents/dashboards)
- ✅ Single workflow (workflow_gajab.txt)
- ✅ Production-ready Playwright execution
- ✅ Complete bug reporting (4 formats)
- ✅ Demo-ready timeouts (5-8s max)
- ✅ Clean repository structure

---

## Team

Built for **TestAthon Bangalore 2026**  
Theme: *Autonomous Testing with Agentic AI*

---

## License

MIT
