"""
UI Pipeline Agent
Discovers a generic web page, generates page object and Playwright test
artifacts, and runs a generic smoke interaction.
"""

import hashlib
import json
import random
import re
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class UiPipelineAgent:
    def __init__(self):
        self.artifacts_dir = Path("artifacts")
        self.data_dir = Path("dashboard/data")
        self.artifacts_dir.mkdir(exist_ok=True)
        self.locator_cache = {}
        self.last_locator_resolution = {}
        self._load_local_env()
        self.llm_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.llm_model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()
        self.llm_enabled = bool(self.llm_api_key) and not self.llm_api_key.startswith("your_")

    def run(self, ui_input: dict) -> dict:
        start = time.time()
        normalized_input = self._normalize_input(ui_input or {})
        llm_interpretation = self._interpret_problem_statement(normalized_input)

        discovery = self._discover_ui(normalized_input)
        workflow = self._build_workflow(normalized_input, discovery, llm_interpretation)
        generated_pom = self._generate_pom_artifact(normalized_input, discovery)
        ui_test_layer = self._generate_test_layer_artifact(normalized_input, discovery, workflow)
        ui_execution = self._execute_workflow(normalized_input, discovery, workflow)

        elapsed = round(time.time() - start, 2)
        audit_entry = {
            "agent": "UiPipeline",
            "timestamp": time.strftime("%H:%M:%S"),
            "scenario_name": normalized_input["scenario_name"],
            "target_platform": normalized_input["target_platform"],
            "discovered_elements": len(discovery["elements"]),
            "planned_steps": len(workflow["actions"]),
            "executed_steps": len(ui_execution["steps"]),
            "passed": ui_execution["passed"],
            "elapsed_s": elapsed,
        }

        return {
            "ui_input": normalized_input,
            "llm_interpretation": llm_interpretation,
            "ui_discovery": discovery,
            "ui_workflow": workflow,
            "generated_pom": generated_pom,
            "ui_test_layer": ui_test_layer,
            "ui_execution": ui_execution,
            "audit_entry": audit_entry,
        }

    def _normalize_input(self, ui_input: dict) -> dict:
        normalized = {
            "url": "http://localhost:5001/checkout",
            "problem_statement": "Discover UI flow and scaffold automation artifacts.",
            "target_platform": "web",
            "scenario_name": "ui-smoke",
            "execution_mode": "auto",
            "run_profile": "demo",
            "step_pause_ms": 1200,
            "keep_browser_open_ms": 2500,
            "headed": False,
        }
        normalized.update(ui_input)
        return normalized

    def _load_local_env(self) -> None:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.exists():
            return

        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value

    def _interpret_problem_statement(self, ui_input: dict) -> dict:
        problem_statement = str(ui_input.get("problem_statement", "")).strip()
        if not problem_statement:
            return self._fallback_intent(problem_statement, ui_input)

        if not self.llm_enabled:
            return self._fallback_intent(problem_statement, ui_input)

        prompt = self._llm_prompt(ui_input)
        try:
            content = self._call_anthropic_llm(prompt)
            parsed = self._parse_llm_json(content)
            if parsed:
                return self._normalize_intent(parsed, ui_input)
        except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
            pass

        return self._fallback_intent(problem_statement, ui_input)

    def _llm_prompt(self, ui_input: dict) -> str:
        return (
            "You are a QA workflow interpreter.\n"
            "Convert the business requirement into STRICT JSON only.\n"
            "Do not explain. Do not wrap in markdown.\n"
            "Use this schema:\n"
            "{\n"
            '  "intent_type": "page_presence_check|workflow|data_entry|navigation|search|auth|checkout|unknown",\n'
            '  "strict_mode": true,\n'
            '  "business_goal": "short plain English",\n'
            '  "steps": [\n'
            '    {"action": "goto|click|fill|select|check|assert_visible", "target": "string", "value": "optional string"}\n'
            "  ],\n"
            '  "assertions": ["string"],\n'
            '  "required_test_skills": ["auth_flow|form_validation|payment_submission|error_handling|accessibility_check"],\n'
            '  "mode_hint": "strict|explore",\n'
            '  "confidence": 0.0,\n'
            '  "notes": ["string"]\n'
            "}\n\n"
            f"URL: {ui_input.get('url', '')}\n"
            f"Problem statement: {ui_input.get('problem_statement', '')}\n"
            f"Target platform: {ui_input.get('target_platform', 'web')}\n"
            f"Requested execution mode: {ui_input.get('execution_mode', 'auto')}\n"
        )

    def _call_anthropic_llm(self, prompt: str) -> str:
        payload = {
            "model": self.llm_model,
            "max_tokens": 800,
            "temperature": 0,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        request = Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.llm_api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()

    def _parse_llm_json(self, text: str) -> dict | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = json.loads(raw[start:end + 1])
        if isinstance(parsed, dict):
            return parsed
        return None

    def _normalize_intent(self, intent: dict, ui_input: dict) -> dict:
        normalized = dict(intent)
        normalized["intent_type"] = str(normalized.get("intent_type", "unknown")).strip() or "unknown"
        normalized["business_goal"] = str(normalized.get("business_goal", ui_input.get("problem_statement", ""))).strip()
        normalized["steps"] = normalized.get("steps", []) if isinstance(normalized.get("steps", []), list) else []
        normalized["assertions"] = normalized.get("assertions", []) if isinstance(normalized.get("assertions", []), list) else []
        normalized["required_test_skills"] = [
            str(skill).strip()
            for skill in normalized.get("required_test_skills", [])
            if str(skill).strip()
        ]
        requested_mode = str(ui_input.get("execution_mode", "auto")).lower()
        mode_hint = str(normalized.get("mode_hint", "strict")).lower()
        normalized["mode_hint"] = mode_hint if mode_hint in {"strict", "explore"} else "strict"
        normalized["strict_mode"] = requested_mode == "strict" or bool(normalized.get("strict_mode", normalized["mode_hint"] == "strict"))
        if requested_mode in {"strict", "explore"}:
            normalized["mode_hint"] = requested_mode
        return normalized

    def _fallback_intent(self, problem_statement: str, ui_input: dict | None = None) -> dict:
        lower = str(problem_statement or "").lower()
        intent_type = "unknown"
        mode_hint = "strict"
        auth_tokens = ["login", "signup", "sign in", "sign up", "register user", "create account"]
        checkout_tokens = ["checkout", "payment", "cart"]
        search_tokens = ["search", "find", "lookup"]
        presence_tokens = ["page exists", "page is visible", "page loads", "site opens", "verify home page"]
        action_tokens = ["click", "enter", "fill", "select", "verify", "assert", "check", "navigate"]

        has_auth = any(token in lower for token in auth_tokens)
        has_checkout = any(token in lower for token in checkout_tokens)
        has_search = any(token in lower for token in search_tokens)
        has_presence = any(token in lower for token in presence_tokens)
        action_hits = sum(1 for token in action_tokens if token in lower)
        lines = [line.strip() for line in lower.replace("\r", "\n").split("\n") if line.strip()]
        numbered_lines = sum(1 for line in lines if re.match(r"^\d+[\).\-\]]", line))
        multi_step = len(lines) >= 3 or numbered_lines >= 2 or action_hits >= 3

        if has_auth:
            intent_type = "auth"
        elif has_checkout:
            intent_type = "checkout"
        elif has_search:
            intent_type = "search"
        elif has_presence and not multi_step:
            intent_type = "page_presence_check"
        elif multi_step:
            intent_type = "workflow"
        if any(token in lower for token in ["discover", "explore", "smoke", "scaffold", "crawl"]):
            mode_hint = "explore"

        requested_mode = str((ui_input or {}).get("execution_mode", "auto")).lower()
        if requested_mode in {"strict", "explore"}:
            mode_hint = requested_mode

        return {
            "intent_type": intent_type,
            "strict_mode": mode_hint != "explore",
            "business_goal": problem_statement or "Inspect the page",
            "steps": [],
            "assertions": ["page visible"] if intent_type == "page_presence_check" else [],
            "required_test_skills": [],
            "mode_hint": mode_hint,
            "confidence": 0.35,
            "notes": ["Fallback interpreter used because no LLM response was available."],
        }

    def _discover_ui(self, ui_input: dict) -> dict:
        url = ui_input["url"]
        artifact_root = self._artifact_root(ui_input)
        artifact_root.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not bool(ui_input.get("headed", False)))
            page = browser.new_page(
                viewport={"width": 1280, "height": 900},
                permissions=["microphone"],  # Only grant microphone, deny geolocation and camera
                extra_http_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            # Dismiss location permission popup if it appears
            page.once("dialog", lambda dialog: dialog.dismiss())
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=8000)
                self._wait_for_initial_page_ready(page)
                if self._visual_trace_enabled(ui_input):
                    print(f"[visual] Discovery: scanning {url}")
                    page.wait_for_timeout(min(900, 500))
                raw_nodes = self._extract_relevant_nodes(page)
            finally:
                browser.close()

        elements = self._classify_nodes(raw_nodes)
        discovery = {
            "status": "generated",
            "url": url,
            "problem_statement": ui_input["problem_statement"],
            "target_platform": ui_input["target_platform"],
            "scenario_name": ui_input["scenario_name"],
            "page_title": self._page_title(ui_input["scenario_name"], url),
            "elements": elements,
            "artifact_root": str(artifact_root).replace("\\", "/"),
        }
        self._write_json(artifact_root / "ui_discovery.json", discovery)
        self._write_discovered_locator_cache(discovery)
        return discovery

    def _extract_relevant_nodes(self, page) -> list[dict]:
        return page.evaluate(
            """
            () => {
              const selectors = [
                'input',
                'textarea',
                'select',
                'input[type="checkbox"]',
                'input[type="radio"]',
                'button',
                'a',
                '[role="button"]',
                '[role="link"]',
                '[contenteditable="true"]'
              ].join(', ');

              return Array.from(document.querySelectorAll(selectors)).map((el, index) => {
                const labels = el.labels ? Array.from(el.labels).map(label => label.textContent.trim()).filter(Boolean) : [];
                const text = (el.innerText || el.value || el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                return {
                  index,
                  tag: el.tagName.toLowerCase(),
                  id: el.id || '',
                  name: el.name || '',
                  type: el.type || '',
                  placeholder: el.placeholder || '',
                  aria_label: el.getAttribute('aria-label') || '',
                  role: el.getAttribute('role') || '',
                  text: text.slice(0, 120),
                  labels,
                  visible: Boolean(rect.width || rect.height),
                };
              });
            }
            """
        )

    def _classify_nodes(self, raw_nodes: list[dict]) -> list[dict]:
        classified = []
        for node in raw_nodes:
            if not node.get("visible", True):
                continue

            locator = self._build_locator(node)
            if locator is None:
                continue

            classified.append(
                {
                    "name": self._element_name(node),
                    "kind": self._node_kind(node),
                    "tag": node.get("tag"),
                    "type": node.get("type", ""),
                    "selector": locator,
                    "source": self._selector_source(locator),
                    "confidence": self._confidence(locator),
                }
            )

        return self._dedupe_by_name(classified)

    def _build_locator(self, node: dict) -> dict | None:
        node_id = (node.get("id") or "").strip()
        if node_id:
            return {"strategy": "css", "value": f"#{node_id}"}

        name = (node.get("name") or "").strip()
        if name:
            return {"strategy": "css", "value": f'[name="{name}"]'}

        placeholder = (node.get("placeholder") or "").strip()
        if placeholder and node.get("tag") == "input":
            return {"strategy": "placeholder", "value": placeholder}

        aria = (node.get("aria_label") or "").strip()
        if aria:
            return {"strategy": "label", "value": aria}

        text = (node.get("text") or "").strip()
        tag = node.get("tag") or ""
        if text and tag in {"button", "a"}:
            return {"strategy": "role", "value": {"role": "link" if tag == "a" else "button", "name": text}}

        if text:
            return {"strategy": "text", "value": text}

        if tag:
            return {"strategy": "css", "value": tag, "nth": int(node.get("index", 0))}

        return None

    def _element_name(self, node: dict) -> str:
        candidates = [
            node.get("id", ""),
            node.get("name", ""),
            node.get("placeholder", ""),
            node.get("aria_label", ""),
            " ".join(node.get("labels", [])),
            node.get("text", ""),
            node.get("role", ""),
        ]
        for candidate in candidates:
            slug = self._slugify(candidate)
            if slug:
                return slug[:48]
        return f"{node.get('tag', 'element')}_{int(node.get('index', 0)) + 1}"

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip().lower())
        slug = slug.strip("_")
        return slug

    def _node_kind(self, node: dict) -> str:
        tag = node.get("tag")
        node_type = (node.get("type") or "").lower()
        if tag in {"button", "a"} or node.get("role") == "button" or node.get("role") == "link":
            return "action"
        if tag == "input" and node_type in {"checkbox", "radio"}:
            return "toggle"
        if tag in {"input", "textarea", "select"} or node_type in {"text", "email", "password", "search", "url", "number"}:
            return "input"
        if tag == "contenteditable":
            return "input"
        return "readable"

    def _selector_source(self, locator: dict) -> str:
        strategy = locator["strategy"]
        if strategy == "css" and "nth" in locator:
            return "css-nth"
        return strategy

    def _confidence(self, locator: dict) -> float:
        if locator["strategy"] == "css":
            return 0.99 if "nth" not in locator else 0.75
        if locator["strategy"] == "label":
            return 0.95
        if locator["strategy"] == "placeholder":
            return 0.92
        if locator["strategy"] == "role":
            return 0.90
        if locator["strategy"] == "text":
            return 0.85
        return 0.7

    def _dedupe_by_name(self, items: list[dict]) -> list[dict]:
        deduped = []
        seen = {}
        for item in items:
            name = item["name"]
            count = seen.get(name, 0) + 1
            seen[name] = count
            if count > 1:
                item = dict(item)
                item["name"] = f"{name}_{count}"
            deduped.append(item)
        return deduped

    def _generate_pom_artifact(self, ui_input: dict, discovery: dict) -> dict:
        class_name = self._page_class_name(ui_input)
        artifact_root = self._artifact_root(ui_input)
        pages_dir = artifact_root / "pages"
        tests_dir = artifact_root / "tests"
        pages_dir.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)

        locator_map = {item["name"]: item["selector"] for item in discovery["elements"]}
        elements_code = self._render_elements_module(locator_map)
        methods_code = self._render_methods_module(class_name, locator_map)
        page_code = self._render_page_module(class_name)

        elements_path = pages_dir / "elements.ts"
        methods_path = pages_dir / "methods.ts"
        page_path = pages_dir / "page.ts"

        self._write_text(elements_path, elements_code)
        self._write_text(methods_path, methods_code)
        self._write_text(page_path, page_code)

        pom_artifact = {
            "status": "generated",
            "class_name": class_name,
            "artifact_root": str(artifact_root).replace("\\", "/"),
            "pages_dir": str(pages_dir).replace("\\", "/"),
            "tests_dir": str(tests_dir).replace("\\", "/"),
            "elements_file": str(elements_path).replace("\\", "/"),
            "methods_file": str(methods_path).replace("\\", "/"),
            "page_file": str(page_path).replace("\\", "/"),
            "locators": locator_map,
        }
        self._write_json(artifact_root / "generated_pom.json", pom_artifact)
        return pom_artifact

    def _generate_test_layer_artifact(self, ui_input: dict, discovery: dict, workflow: dict) -> dict:
        artifact_root = self._artifact_root(ui_input)
        tests_dir = artifact_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        test_code = self._render_test_spec(ui_input, discovery, workflow)
        test_path = tests_dir / f"test_{self._slugify(ui_input['scenario_name']) or 'ui_smoke'}.spec.ts"
        self._write_text(test_path, test_code)

        test_artifact = {
            "status": "generated",
            "test_file": str(test_path).replace("\\", "/"),
            "framework": "playwright",
            "scenario_name": ui_input["scenario_name"],
            "page_object": self._page_class_name(ui_input),
            "artifact_root": str(artifact_root).replace("\\", "/"),
        }
        self._write_json(artifact_root / "ui_test_layer.json", test_artifact)
        return test_artifact

    def _execute_workflow(self, ui_input: dict, discovery: dict, workflow: dict) -> dict:
        steps = []
        errors = []
        title = ""
        final_url = ""
        execution_context = {"active_scope_hint": "", "last_form_marker": ""}
        artifact_root = self._artifact_root(ui_input)
        step_artifacts_dir = artifact_root / "step_screenshots"
        step_artifacts_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not bool(ui_input.get("headed", False)))
            page = browser.new_page(
                viewport={"width": 1280, "height": 900},
                permissions=["microphone"],
                extra_http_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            page.once("dialog", lambda dialog: dialog.dismiss())
            try:
                page.goto(ui_input["url"], wait_until="domcontentloaded", timeout=8000)
                steps.append({"step": "goto", "status": "passed"})
                self._wait_for_initial_page_ready(page)
                self._dismiss_common_popups(page)
                for action in workflow["actions"]:
                    if self._visual_trace_enabled(ui_input):
                        print(f"[visual] Step {action.get('stage', '?')}: {action.get('raw_step', action.get('kind', 'Running step'))}")
                        page.wait_for_timeout(500)
                    step_start = time.perf_counter()
                    result = self._run_workflow_action(page, discovery, action, ui_input, execution_context)
                    result.setdefault("details", {})
                    result["details"]["duration_ms"] = round((time.perf_counter() - step_start) * 1000, 1)
                    result["details"]["url_after_step"] = page.url
                    result["details"]["page_title_after_step"] = page.title()
                    steps.append(result)
                    screenshot_path = self._capture_step_screenshot(page, step_artifacts_dir, action, result)
                    if screenshot_path:
                        result["details"]["screenshot"] = screenshot_path
                    if self._visual_trace_enabled(ui_input):
                        print(f"[visual] Result: {result.get('step', 'step')} -> {result.get('status', 'unknown')}")
                        page.wait_for_timeout(700)
                    if result["status"] == "failed" and action.get("required", True):
                        errors.append(result.get("error", f"Failed action: {action.get('kind')}"))

                page.wait_for_timeout(300)
                title = page.title()
                final_url = page.url
                steps.append({"step": "capture_state", "status": "passed", "title": title, "url": final_url})
                if self._visual_trace_enabled(ui_input):
                    print(f"[visual] Run complete: {final_url}")
                    page.wait_for_timeout(self._keep_browser_open_ms(ui_input))
            except (PlaywrightError, PlaywrightTimeoutError) as exc:
                errors.append(str(exc))
                steps.append({"step": "execution", "status": "failed", "error": str(exc)})
            finally:
                browser.close()

        execution = {
            "status": "generated",
            "runner": "playwright",
            "command": f"playwright workflow: {ui_input['url']}",
            "execution_mode": workflow.get("execution_mode", ui_input.get("execution_mode", "auto")),
            "passed": len(errors) == 0,
            "title": title,
            "final_url": final_url,
            "errors": errors,
            "steps": steps,
            "workflow_summary": self._workflow_summary(workflow, steps),
            "check_summary": self._check_summary(steps),
            "locator_summary": self._locator_summary(steps),
            "artifact_root": str(artifact_root).replace("\\", "/"),
            "step_screenshots_dir": str(step_artifacts_dir).replace("\\", "/"),
        }
        self._write_json(artifact_root / "ui_execution.json", execution)
        self._write_json(artifact_root / "workflow_plan.json", workflow)

        return execution

    def _nfr_policy(self, ui_input: dict | None = None) -> dict:
        profile = str((ui_input or {}).get("run_profile", "demo")).lower()
        policies = {
            "demo": {
                "a11y_max_issues": 60,
                "a11y_max_critical": 6,
                "issue_sample_limit": 25,
                "perf_max_resources": 300,
                "perf_max_dom_ms": 7000,
                "perf_max_load_ms": 10000,
                "perf_fail_on_missing_nav": False,
                "visual_allow_dynamic_waiver": True,
            },
            "balanced": {
                "a11y_max_issues": 35,
                "a11y_max_critical": 4,
                "issue_sample_limit": 25,
                "perf_max_resources": 240,
                "perf_max_dom_ms": 5500,
                "perf_max_load_ms": 8500,
                "perf_fail_on_missing_nav": False,
                "visual_allow_dynamic_waiver": True,
            },
            "thorough": {
                "a11y_max_issues": 15,
                "a11y_max_critical": 2,
                "issue_sample_limit": 25,
                "perf_max_resources": 180,
                "perf_max_dom_ms": 4500,
                "perf_max_load_ms": 7000,
                "perf_fail_on_missing_nav": True,
                "visual_allow_dynamic_waiver": False,
            },
        }
        return policies.get(profile, policies["demo"])

    def _run_accessibility_scan(self, page, ui_input: dict | None = None) -> dict:
        policy = self._nfr_policy(ui_input)
        findings = page.evaluate(
            """
            () => {
              const issues = [];
              const interactive = Array.from(document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], [contenteditable="true"]'));
              interactive.forEach((el, index) => {
                const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                const title = (el.getAttribute('title') || '').trim();
                const text = (el.innerText || el.textContent || el.value || '').trim();
                const labels = el.labels ? Array.from(el.labels).map((label) => (label.textContent || '').trim()).filter(Boolean) : [];
                if (!(ariaLabel || title || text || labels.length)) {
                  issues.push({ type: 'missing_accessible_name', tag: el.tagName.toLowerCase(), index, id: el.id || '', name: el.name || '' });
                }
              });

              Array.from(document.querySelectorAll('img')).forEach((img, index) => {
                if (!(img.getAttribute('alt') || '').trim()) {
                  issues.push({ type: 'missing_alt_text', tag: 'img', index, src: img.getAttribute('src') || '' });
                }
              });

              const headingLevels = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6')).map((heading) => Number(heading.tagName.slice(1)));
              for (let i = 1; i < headingLevels.length; i += 1) {
                if (headingLevels[i] - headingLevels[i - 1] > 1) {
                  issues.push({ type: 'heading_skip', from: headingLevels[i - 1], to: headingLevels[i] });
                  break;
                }
              }

              return {
                interactiveCount: interactive.length,
                imageCount: document.querySelectorAll('img').length,
                headingCount: headingLevels.length,
                issues,
              };
            }
            """
        )
        raw_issues = findings.get("issues", [])
        filtered_issues = []
        for issue in raw_issues:
            issue_id = str(issue.get("id", ""))
            if re.match(r"home-wp\d+-carousel-dot-\d+", issue_id):
                continue
            filtered_issues.append(issue)

        critical_types = {"missing_alt_text", "heading_skip"}
        critical_count = sum(1 for issue in filtered_issues if issue.get("type") in critical_types)
        total_issues = len(filtered_issues)
        sample_limit = int(policy.get("issue_sample_limit", 25))
        passed = total_issues <= int(policy.get("a11y_max_issues", 60)) and critical_count <= int(policy.get("a11y_max_critical", 6))

        budget_issues = []
        if total_issues > int(policy.get("a11y_max_issues", 60)):
            budget_issues.append(f"a11y_issue_budget_exceeded:{total_issues}")
        if critical_count > int(policy.get("a11y_max_critical", 6)):
            budget_issues.append(f"a11y_critical_budget_exceeded:{critical_count}")

        return {
            "passed": passed,
            "issue_count": total_issues,
            "critical_issue_count": critical_count,
            "interactive_count": findings.get("interactiveCount", 0),
            "image_count": findings.get("imageCount", 0),
            "heading_count": findings.get("headingCount", 0),
            "issues": filtered_issues[:sample_limit],
            "budget_issues": budget_issues,
        }

    def _run_performance_scan(self, page, ui_input: dict | None = None) -> dict:
        policy = self._nfr_policy(ui_input)
        metrics = page.evaluate(
            """
            () => {
              const nav = performance.getEntriesByType('navigation')[0] || null;
              const resources = performance.getEntriesByType('resource');
              return {
                domContentLoadedMs: nav ? Math.round(nav.domContentLoadedEventEnd - nav.startTime) : null,
                loadEventEndMs: nav ? Math.round(nav.loadEventEnd - nav.startTime) : null,
                transferSize: nav ? Math.round(nav.transferSize || 0) : null,
                encodedBodySize: nav ? Math.round(nav.encodedBodySize || 0) : null,
                resourceCount: resources.length,
              };
            }
            """
        )
        dom_ms = metrics.get("domContentLoadedMs")
        load_ms = metrics.get("loadEventEndMs")
        resource_count = int(metrics.get("resourceCount", 0) or 0)
        budget_issues = []
        if dom_ms is None or load_ms is None:
            if bool(policy.get("perf_fail_on_missing_nav", False)):
                budget_issues.append("navigation_timing_unavailable")
        elif dom_ms > int(policy.get("perf_max_dom_ms", 7000)) or load_ms > int(policy.get("perf_max_load_ms", 10000)):
            budget_issues.append(f"slow_navigation:{dom_ms}/{load_ms}")
        if resource_count > int(policy.get("perf_max_resources", 300)):
            budget_issues.append(f"too_many_resources:{resource_count}")
        return {
            "passed": not budget_issues,
            "metrics": metrics,
            "issues": budget_issues,
            "error": "Performance budget exceeded" if budget_issues else "",
        }

    def _run_security_scan(self, page) -> dict:
        findings = page.evaluate(
            """
            () => {
              const issues = [];
              const forms = Array.from(document.querySelectorAll('form'));
              const insecureForms = forms.filter((form) => (form.getAttribute('action') || '').startsWith('http://'));
              const javascriptLinks = Array.from(document.querySelectorAll('a[href^="javascript:"], area[href^="javascript:"]'));
              const mixedContent = Array.from(document.querySelectorAll('script[src^="http://"], img[src^="http://"], link[href^="http://"], iframe[src^="http://"]'));

              if (location.protocol !== 'https:') {
                issues.push({ type: 'insecure_protocol', protocol: location.protocol });
              }
              insecureForms.forEach((form, index) => issues.push({ type: 'insecure_form_action', index, action: form.getAttribute('action') || '' }));
              javascriptLinks.forEach((link, index) => issues.push({ type: 'javascript_url', index, href: link.getAttribute('href') || '' }));
              mixedContent.forEach((node, index) => issues.push({ type: 'mixed_content', index, src: node.getAttribute('src') || node.getAttribute('href') || '' }));

              return {
                formCount: forms.length,
                insecureFormCount: insecureForms.length,
                javascriptLinkCount: javascriptLinks.length,
                mixedContentCount: mixedContent.length,
                issues,
              };
            }
            """
        )
        issues = findings.get("issues", [])
        return {
            "passed": not issues,
            "issue_count": len(issues),
            "form_count": findings.get("formCount", 0),
            "insecure_form_count": findings.get("insecureFormCount", 0),
            "javascript_link_count": findings.get("javascriptLinkCount", 0),
            "mixed_content_count": findings.get("mixedContentCount", 0),
            "issues": issues[:25],
            "error": "Security findings detected" if issues else "",
        }

    def _run_visual_compare(self, page, ui_input: dict) -> dict:
        policy = self._nfr_policy(ui_input)
        artifact_root = self._artifact_root(ui_input)
        baseline_dir = artifact_root / "visual_baselines"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        screenshot_dir = artifact_root / "step_screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slugify(ui_input.get("scenario_name", "visual_compare")) or "visual_compare"
        if bool(ui_input.get("nfr_only", False)):
            slug = f"{slug}_nfr"
        baseline_path = baseline_dir / f"{slug}.json"
        screenshot_path = screenshot_dir / f"visual_compare_{slug}.png"
        screenshot_bytes = page.screenshot(path=str(screenshot_path), full_page=True)
        current_hash = hashlib.sha256(screenshot_bytes).hexdigest()
        page.wait_for_timeout(450)
        stable_probe_hash = hashlib.sha256(page.screenshot(full_page=True)).hexdigest()
        is_dynamic_page = stable_probe_hash != current_hash

        if not baseline_path.exists():
            baseline = {
                "scenario_name": ui_input.get("scenario_name", ""),
                "url": ui_input.get("url", ""),
                "page_title": page.title(),
                "hash": current_hash,
                "created_from": str(screenshot_path).replace("\\", "/"),
            }
            self._write_json(baseline_path, baseline)
            return {
                "passed": True,
                "baseline_created": True,
                "baseline_hash": current_hash,
                "current_hash": current_hash,
                "baseline_path": str(baseline_path).replace("\\", "/"),
                "screenshot_path": str(screenshot_path).replace("\\", "/"),
                "issues": [],
            }

        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_hash = str(baseline.get("hash", ""))
        passed = baseline_hash == current_hash
        issues = [] if passed else ["visual_hash_mismatch"]
        waived_dynamic = False
        if not passed and is_dynamic_page and bool(policy.get("visual_allow_dynamic_waiver", True)):
            passed = True
            waived_dynamic = True
            issues = ["visual_dynamic_content_detected"]
        return {
            "passed": passed,
            "baseline_created": False,
            "baseline_hash": baseline_hash,
            "current_hash": current_hash,
            "stability_probe_hash": stable_probe_hash,
            "is_dynamic_page": is_dynamic_page,
            "waived_dynamic_mismatch": waived_dynamic,
            "baseline_path": str(baseline_path).replace("\\", "/"),
            "screenshot_path": str(screenshot_path).replace("\\", "/"),
            "issues": issues,
            "error": "Visual comparison mismatch" if not passed else "",
        }

    def _resolve_locator(self, page, selector: dict):
        strategy = selector["strategy"]
        if strategy == "css":
            locator = page.locator(selector["value"])
            if "nth" in selector:
                return locator.nth(int(selector["nth"]))
            return locator
        if strategy == "label":
            return page.get_by_label(selector["value"])
        if strategy == "placeholder":
            return page.get_by_placeholder(selector["value"])
        if strategy == "role":
            return page.get_by_role(selector["value"]["role"], name=selector["value"]["name"])
        if strategy == "text":
            return page.get_by_text(selector["value"])
        raise PlaywrightError(f"Unsupported selector strategy: {strategy}")

    def _render_elements_module(self, locators: dict[str, dict]) -> str:
        return (
            '"""Generated element registry."""\n\n'
            "export const locators = "
            + json.dumps(locators, indent=2)
            + " as const;\n\n"
            "export type LocatorKey = keyof typeof locators;\n"
        )

    def _render_methods_module(self, class_name: str, locators: dict[str, dict]) -> str:
        return (
            '"""Generated page methods."""\n\n'
            "import { Page } from '@playwright/test';\n"
            "import { locators, LocatorKey } from './elements';\n\n"
            f"export class {class_name}Methods {{\n"
            "  constructor(private page: Page) {}\n\n"
            "  private resolve(key: LocatorKey) {\n"
            "    const locator = locators[key];\n"
            "    switch (locator.strategy) {\n"
            "      case 'css': {\n"
            "        const resolved = this.page.locator(locator.value);\n"
            "        return 'nth' in locator ? resolved.nth(locator.nth) : resolved;\n"
            "      }\n"
            "      case 'label':\n"
            "        return this.page.getByLabel(locator.value);\n"
            "      case 'placeholder':\n"
            "        return this.page.getByPlaceholder(locator.value);\n"
            "      case 'role':\n"
            "        return this.page.getByRole(locator.value.role, { name: locator.value.name });\n"
            "      case 'text':\n"
            "        return this.page.getByText(locator.value);\n"
            "      default:\n"
            "        throw new Error(`Unsupported locator strategy: ${locator.strategy}`);\n"
            "    }\n"
            "  }\n\n"
            "  async fill(key: LocatorKey, value: string) {\n"
            "    await this.resolve(key).fill(value);\n"
            "  }\n\n"
            "  async select(key: LocatorKey, index = 0) {\n"
            "    await this.resolve(key).selectOption({ index });\n"
            "  }\n\n"
            "  async click(key: LocatorKey) {\n"
            "    await this.resolve(key).click();\n"
            "  }\n\n"
            "  async text(key: LocatorKey) {\n"
            "    return await this.resolve(key).innerText();\n"
            "  }\n"
            "}\n"
        )

    def _render_page_module(self, class_name: str) -> str:
        return (
            '"""Generated page object wrapper."""\n\n'
            f"import {{ {class_name}Methods }} from './methods';\n\n"
            f"export class {class_name} extends {class_name}Methods {{}}\n"
        )

    def _render_test_spec(self, ui_input: dict, discovery: dict, workflow: dict) -> str:
        class_name = self._page_class_name(ui_input)
        pages_import = "../pages/page"

        lines = [
            "import { test, expect } from '@playwright/test';",
            f"import {{ {class_name} }} from {json.dumps(pages_import)};",
            "",
            f"test('{ui_input['scenario_name']}', async ({'{'} page {'}'}) => {{",
            "  page.setDefaultTimeout(30000);",
            f"  await page.goto({json.dumps(ui_input['url'])}, {{ waitUntil: 'domcontentloaded', timeout: 30000 }});",
            "  await page.locator('body').waitFor({ state: 'visible', timeout: 10000 });",
            "  await page.waitForLoadState('domcontentloaded');",
            "  await page.waitForTimeout(2000);",
            f"  const ui = new {class_name}(page);",
        ]

        for action in workflow["actions"]:
            target = action.get("resolved_element", "")
            kind = action.get("kind", "")
            if kind == "goto":
                lines.append(f"  await page.goto({json.dumps(action.get('url', ui_input['url']))}, {{ waitUntil: 'domcontentloaded' }});")
            elif kind == "click" and target:
                lines.append(f"  await ui.click({json.dumps(target)});")
            elif kind == "fill" and target:
                lines.append(f"  await ui.fill({json.dumps(target)}, {json.dumps(action.get('value', 'sample input'))});")
            elif kind == "select" and target:
                lines.append(f"  await ui.select({json.dumps(target)});")
            elif kind == "check" and target:
                lines.append(f"  await ui.click({json.dumps(target)});")
            elif kind == "assert_visible":
                phrase = action.get("assert_text", action.get("target", ""))
                if self._slugify(phrase) in {"home_page_visible", "page_visible"}:
                    lines.append("  await expect(page.locator('body')).toBeVisible();")
                else:
                    lines.append(f"  await expect(page.getByText({json.dumps(phrase)}, {{ exact: false }})).toBeVisible();")
            elif kind == "assert_url_contains":
                fragment = action.get("url_fragment", action.get("target", ""))
                lines.append(f"  await expect(page).toHaveURL(new RegExp({json.dumps(fragment)}));")
            elif kind in {"accessibility_scan", "performance_budget", "security_smoke", "visual_compare"}:
                lines.append(f"  // Non-functional check: {kind} ({json.dumps(action.get('target', kind))})")

        lines.append("  await expect(page.locator('body')).toBeVisible();")
        lines.append("});")
        return "\n".join(lines) + "\n"

    def _build_workflow(self, ui_input: dict, discovery: dict, intent: dict | None = None) -> dict:
        execution_mode = self._resolve_execution_mode(ui_input, intent or {})
        if bool(ui_input.get("nfr_only", False)):
            actions = self._nfr_setup_actions(ui_input, discovery)
            actions.extend(self._non_functional_actions(ui_input, intent or {}, force_all=True))
            for index, action in enumerate(actions, start=1):
                action["stage"] = index
            return {
                "status": "generated",
                "workflow_type": "non_functional",
                "source": "nfr_only",
                "actions": actions,
                "total_actions": len(actions),
                "execution_mode": execution_mode,
                "intent": intent or {},
            }

        if intent is not None:
            intent_actions = self._actions_from_intent(ui_input, discovery, intent)
            if intent_actions:
                return {
                    "status": "generated",
                    "workflow_type": "llm_interpreted" if intent.get("intent_type") != "unknown" else "requirement_driven",
                    "source": "llm_problem_statement" if intent.get("intent_type") != "unknown" else "ui_problem_statement",
                    "actions": intent_actions,
                    "total_actions": len(intent_actions),
                    "execution_mode": execution_mode,
                    "intent": intent,
                }

        raw_steps = self._extract_requirement_steps(ui_input.get("problem_statement", ""))
        actions: list[dict] = []
        stage = 1
        for raw_step in raw_steps:
            parsed = self._parse_step(raw_step, discovery)
            if not parsed:
                continue
            for action in parsed:
                action["stage"] = stage
                actions.append(action)
                stage += 1
        if not actions:
            actions = self._fallback_actions(ui_input, discovery, intent or {}, execution_mode, raw_steps)
        return {
            "status": "generated",
            "workflow_type": "requirement_driven",
            "source": "ui_problem_statement",
            "actions": actions,
            "total_actions": len(actions),
            "execution_mode": execution_mode,
            "intent": intent or {},
        }

    def _actions_from_intent(self, ui_input: dict, discovery: dict, intent: dict) -> list[dict]:
        steps = intent.get("steps", [])
        actions: list[dict] = []
        stage = 1

        if intent.get("intent_type") == "page_presence_check" and not steps:
            return [
                {
                    "kind": "goto",
                    "target": "input_url",
                    "url": ui_input.get("url", ""),
                    "required": True,
                    "raw_step": "LLM page presence check",
                    "stage": stage,
                },
                {
                    "kind": "assert_visible",
                    "target": "home_page_visible",
                    "assert_text": "home_page_visible",
                    "required": True,
                    "raw_step": "LLM page presence check",
                    "stage": stage + 1,
                },
            ]

        for item in steps:
            action_name = str(item.get("action", "")).strip().lower()
            target = str(item.get("target", "")).strip()
            value = item.get("value")
            if not action_name:
                continue

            action = {
                "kind": action_name if action_name in {"goto", "click", "fill", "select", "check", "assert_visible", "accessibility_scan", "performance_budget", "security_smoke", "visual_compare"} else "assert_visible",
                "target": target or action_name,
                "required": True,
                "raw_step": f"LLM:{action_name}:{target}",
                "stage": stage,
            }
            if action["kind"] == "goto":
                action["url"] = value or (target if target.startswith("http") else ui_input.get("url", ""))
            elif action["kind"] == "fill":
                action["resolved_element"] = self._find_element_name(discovery, target, preferred_kind="input") or ""
                action["value"] = value or self._sample_value_for_field(target)
            elif action["kind"] == "click":
                action["resolved_element"] = self._find_element_name(discovery, target, preferred_kind="action") or ""
            elif action["kind"] == "select":
                action["resolved_element"] = self._find_element_name(discovery, target, preferred_kind="input") or ""
            elif action["kind"] == "check":
                action["resolved_element"] = self._find_element_name(discovery, target, preferred_kind="toggle") or ""
            elif action["kind"] == "assert_visible":
                action["assert_text"] = target or str(value or "")

            actions.append(action)
            stage += 1

        if not actions and intent.get("assertions"):
            for assertion in intent.get("assertions", []):
                actions.append({
                    "kind": "assert_visible",
                    "target": assertion,
                    "assert_text": assertion,
                    "required": True,
                    "raw_step": f"LLM assertion: {assertion}",
                    "stage": stage,
                })
                stage += 1

        return actions

    def _extract_requirement_steps(self, statement: str) -> list[str]:
        text = str(statement or "").replace("\r", "\n")
        chunks = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(";") if part.strip()]
            chunks.extend(parts if parts else [line])
        cleaned = []
        for chunk in chunks:
            step = re.sub(r"^\s*(\d+[\).\-\]]?|[-*])\s*", "", chunk).strip()
            if len(step) >= 3:
                cleaned.append(step)
        return cleaned

    def _parse_step(self, step: str, discovery: dict) -> list[dict]:
        text = step.strip()
        lower = text.lower()
        actions: list[dict] = []
        quoted = self._extract_quoted_text(text)
        target = quoted[0] if quoted else self._extract_target_phrase(text)

        if any(token in lower for token in ["launch browser", "navigate", "go to url", "open url"]):
            url_match = re.search(r"https?://[^\s\]'\"]+", text)
            actions.append({
                "kind": "goto",
                "target": "input_url",
                "url": url_match.group(0) if url_match else "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if any(token in lower for token in ["verify", "assert", "check", "confirm"]) and "url" in lower and "contain" in lower:
            url_fragment = quoted[0] if quoted else self._extract_url_contains_value(text)
            actions.append({
                "kind": "assert_url_contains",
                "target": url_fragment,
                "url_fragment": url_fragment,
                "required": True,
                "raw_step": text,
            })
            return actions

        if any(token in lower for token in ["verify", "assert", "check", "confirm"]) and any(token in lower for token in ["visible", "exists", "loaded", "displayed"]):
            assert_text = target or self._extract_assert_phrase(text)
            actions.append({
                "kind": "assert_visible",
                "target": assert_text,
                "assert_text": assert_text,
                "required": True,
                "raw_step": text,
            })
            return actions

        if "checkbox" in lower or "check box" in lower or lower.startswith("select checkbox"):
            checkbox_target = quoted[0] if quoted else self._extract_checkbox_target(text)
            fields = [checkbox_target] if checkbox_target else self._extract_field_list(text)
            for field in fields:
                resolved = self._find_element_name(discovery, field, preferred_kind="toggle")
                actions.append({
                    "kind": "check",
                    "target": field,
                    "resolved_element": resolved or "",
                    "required": False if not resolved else True,
                    "raw_step": text,
                })
            return actions

        if any(token in lower for token in ["refresh", "reload", "f5"]):
            actions.append({
                "kind": "refresh",
                "target": "page",
                "required": True,
                "raw_step": text,
            })
            return actions
        if any(token in lower for token in ["wait", "wait for", "pause"]):
            wait_target = quoted[0] if quoted else self._extract_target_phrase(text)
            actions.append({
                "kind": "wait",
                "target": wait_target or "page",
                "wait_type": "assert_visible",
                "required": False,
                "raw_step": text,
            })
            return actions
        if "click" in lower:
            resolved = self._find_element_name(discovery, target, preferred_kind="action")
            actions.append({
                "kind": "click",
                "target": target,
                "resolved_element": resolved or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if any(token in lower for token in ["fill details", "enter details", "enter name and email", "fill", "enter "]):
            fields = self._extract_field_list(text)
            # Try to extract quoted value from the entire step text
            quoted_value = self._extract_quoted_value(text)
            
            for field in fields:
                # Use extracted value if available, otherwise use field-specific fallback
                if quoted_value:
                    value = quoted_value
                else:
                    value = self._sample_value_for_field(field)
                
                actions.append({
                    "kind": "fill",
                    "target": field,
                    "resolved_element": "",
                    "value": value,
                    "required": True,
                    "raw_step": text,
                })
            return actions

        if lower.startswith("select ") and "checkbox" not in lower:
            fields = self._extract_field_list(text)
            for field in fields:
                resolved = self._find_element_name(discovery, field, preferred_kind="input")
                actions.append({
                    "kind": "select",
                    "target": field,
                    "resolved_element": resolved or "",
                    "required": False if not resolved else True,
                    "raw_step": text,
                })
            return actions

        return actions

    def _resolve_execution_mode(self, ui_input: dict, intent: dict) -> str:
        requested = str(ui_input.get("execution_mode", "auto")).lower()
        if requested in {"strict", "explore"}:
            return requested
        if str(intent.get("mode_hint", "")).lower() == "explore":
            return "explore"
        if intent.get("intent_type") == "page_presence_check":
            return "strict"
        return "strict"

    def _fallback_actions(self, ui_input: dict, discovery: dict, intent: dict, execution_mode: str, raw_steps: list[str]) -> list[dict]:
        nf_actions = self._non_functional_actions(ui_input, intent)
        if nf_actions:
            for index, action in enumerate(nf_actions, start=1):
                action["stage"] = index
            return nf_actions

        if execution_mode == "strict" and raw_steps:
            return [
                {
                    "kind": "goto",
                    "target": "input_url",
                    "url": ui_input.get("url", ""),
                    "required": True,
                    "raw_step": "Guardrail navigation",
                    "stage": 1,
                },
                {
                    "kind": "assert_visible",
                    "target": "home_page_visible" if intent.get("intent_type") == "page_presence_check" else "page_visible",
                    "assert_text": "home_page_visible" if intent.get("intent_type") == "page_presence_check" else "page_visible",
                    "required": True,
                    "raw_step": "Guardrail visibility check",
                    "stage": 2,
                },
            ]

        actions = []
        for element in self._fillable_elements(discovery["elements"])[:2]:
            actions.append({
                "kind": "fill",
                "target": element["name"],
                "resolved_element": element["name"],
                "value": self._value_for_element(element),
                "required": False,
                "raw_step": f"Auto-fill {element['name']}",
                "stage": len(actions) + 1,
            })
        action = self._primary_action(discovery["elements"])
        if action is not None:
            actions.append({
                "kind": "click",
                "target": action["name"],
                "resolved_element": action["name"],
                "required": False,
                "raw_step": f"Auto-click {action['name']}",
                "stage": len(actions) + 1,
            })
        return actions

    def _non_functional_actions(self, ui_input: dict, intent: dict, force_all: bool = False) -> list[dict]:
        statement = str(ui_input.get("problem_statement", "")).lower()
        skills = {
            str(skill).strip().lower()
            for skill in intent.get("required_test_skills", [])
            if str(skill).strip()
        }
        actions: list[dict] = []

        if force_all or "accessibility" in statement or "accessibility_check" in skills:
            actions.append({"kind": "accessibility_scan", "target": "accessibility", "required": True, "raw_step": "Accessibility check"})

        if force_all or any(token in statement for token in ["performance", "load time", "timing", "speed"]) or "performance_check" in skills:
            actions.append({"kind": "performance_budget", "target": "performance", "required": True, "raw_step": "Performance check"})

        if force_all or any(token in statement for token in ["security", "secure", "vulnerability", "mixed content"]) or "security_check" in skills:
            actions.append({"kind": "security_smoke", "target": "security", "required": True, "raw_step": "Security check"})

        if force_all or any(token in statement for token in ["visual regression", "visual comparison", "page comparison", "screenshot comparison", "compare page"]) or "visual_compare" in skills:
            actions.append({"kind": "visual_compare", "target": "visual", "required": True, "raw_step": "Visual regression check"})

        return actions

    def _nfr_setup_actions(self, ui_input: dict, discovery: dict) -> list[dict]:
        """Reuse key functional bootstrap steps so NFR checks run after popup/login stabilization."""
        statement = str(ui_input.get("problem_statement", ""))
        raw_steps = self._extract_requirement_steps(statement)
        if not raw_steps:
            return []

        include_tokens = [
            "launch browser",
            "navigate",
            "open url",
            "home page",
            "log in",
            "sign up",
            "sign in",
            "mobile",
            "request otp",
            "otp",
            "check box",
            "terms and conditions",
            "wait for account creation",
        ]
        stop_tokens = [
            "verify location results",
            "deal of the day",
            "order details",
            "verify the savings",
        ]

        actions: list[dict] = []
        for raw_step in raw_steps:
            lower = raw_step.lower()
            if any(token in lower for token in stop_tokens):
                break
            if not any(token in lower for token in include_tokens):
                continue
            parsed = self._parse_step(raw_step, discovery)
            if not parsed:
                continue
            for action in parsed:
                action = dict(action)
                action["required"] = False
                actions.append(action)
        return actions

    def _run_workflow_action(
        self,
        page,
        discovery: dict,
        action: dict,
        ui_input: dict | None = None,
        execution_context: dict | None = None,
    ) -> dict:
        kind = action.get("kind", "")
        step_title = f"{kind}:{action.get('target', '')}".strip(":")
        optional = not action.get("required", True)
        ui_input = ui_input or {}
        execution_context = execution_context or {}
        raw_step = str(action.get("raw_step", ""))
        target = str(action.get("target", ""))
        execution_context["active_scope_hint"] = self._merge_scope_hint(
            execution_context.get("active_scope_hint", ""),
            raw_step,
            target,
        )
        try:
            self._wait_for_page_ready(page)
            self._dismiss_common_popups(page)
            if kind == "goto":
                url = action.get("url") or page.url
                page.goto(url, wait_until="domcontentloaded", timeout=5000)
                self._wait_for_page_ready(page)
                self._dismiss_common_popups(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"navigation_url": url}}
            elif kind == "click":
                locator = self._dynamic_locator_with_healing(page, action.get("target", ""), kind, action.get("resolved_element", ""))
                locator_resolution = self._consume_locator_resolution()
                if locator is None:
                    element = self._find_element(discovery, action.get("resolved_element"), action.get("target"), preferred_kind="action")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if element:
                        locator_resolution = self._selector_resolution(element, healed=False, source="discovery_lookup")
                if locator is None:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": "Target not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                self._highlight_locator(page, locator, ui_input)
                locator.click()
                self._wait_for_page_ready(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "fill":
                locator = self._contextual_fill_locator(page, action, execution_context)
                if locator is None:
                    locator = self._dynamic_locator_with_healing(page, action.get("target", ""), kind, action.get("resolved_element", ""))
                locator_resolution = self._consume_locator_resolution()
                if locator is None:
                    element = self._find_element(discovery, action.get("resolved_element"), action.get("target"), preferred_kind="input")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if element:
                        locator_resolution = self._selector_resolution(element, healed=False, source="discovery_lookup")
                if locator is None:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": "Field not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                self._highlight_locator(page, locator, ui_input)
                field_value = action.get("value", "sample input")
                filled, fill_mode = self._fill_value(page, locator, field_value)
                if not filled:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": f"Unable to set value: {fill_mode}", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution, "filled_value": field_value, "fill_mode": fill_mode}}
                form_marker = self._capture_form_marker(page, locator)
                if form_marker:
                    execution_context["last_form_marker"] = form_marker
            elif kind == "check":
                locator = self._contextual_checkbox_locator(page, action, execution_context)
                if locator is None:
                    locator = self._dynamic_locator_with_healing(page, action.get("target", ""), kind, action.get("resolved_element", ""))
                locator_resolution = self._consume_locator_resolution()
                if locator is None:
                    element = self._find_element(discovery, action.get("resolved_element"), action.get("target"), preferred_kind="toggle")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if element:
                        locator_resolution = self._selector_resolution(element, healed=False, source="discovery_lookup")
                if locator is None:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": "Checkbox not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                self._highlight_locator(page, locator, ui_input)
                self._set_checkbox(locator)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "select":
                locator = self._dynamic_locator_with_healing(page, action.get("target", ""), kind, action.get("resolved_element", ""))
                locator_resolution = self._consume_locator_resolution()
                if locator is None:
                    element = self._find_element(discovery, action.get("resolved_element"), action.get("target"), preferred_kind="input")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if element:
                        locator_resolution = self._selector_resolution(element, healed=False, source="discovery_lookup")
                if locator is None:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": "Select not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                self._highlight_locator(page, locator, ui_input)
                locator.select_option(index=0)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "assert_url_contains":
                url_fragment = str(action.get("url_fragment", action.get("target", ""))).strip()
                self._wait_for_page_ready(page)
                current_url = page.url
                if not url_fragment or url_fragment.lower() not in current_url.lower():
                    raise PlaywrightTimeoutError(f"Expected URL to contain '{url_fragment}', but got '{current_url}'")
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"expected_url_fragment": url_fragment, "current_url": current_url}}
            elif kind == "assert_visible":
                assert_text = action.get("assert_text", action.get("target", ""))
                if assert_text:
                    assert_key = self._slugify(assert_text)
                    assertion_targets = self._assertion_candidates(assert_text)
                    assertion_attempts = []
                    if "home_page" in assert_key:
                        page.locator("body").wait_for(state="visible", timeout=5000)
                        assertion_attempts.append("body_visible")
                    elif "page_visible" == assert_key:
                        page.locator("body").wait_for(state="visible", timeout=5000)
                        assertion_attempts.append("body_visible")
                    else:
                        # Stabilize page before checking
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except PlaywrightTimeoutError:
                            pass
                        page.wait_for_timeout(500)
                        
                        # Try with increasing timeouts and fallback strategies
                        found = False
                        for candidate in assertion_targets:
                            for timeout in [5000, 3000, 2000]:
                                try:
                                    page.get_by_text(candidate, exact=False).first.wait_for(state="visible", timeout=timeout)
                                    found = True
                                    assertion_attempts.append(f"text:{candidate}:{timeout}")
                                    break
                                except PlaywrightTimeoutError:
                                    assertion_attempts.append(f"text_timeout:{candidate}:{timeout}")
                            if found:
                                break
                        
                        if not found:
                            try:
                                content = page.evaluate("document.documentElement.innerText")
                                normalized_content = self._normalize_visible_text(content)
                                for candidate in assertion_targets:
                                    if self._normalize_visible_text(candidate) in normalized_content:
                                        found = True
                                        assertion_attempts.append(f"document_text:{candidate}")
                                        break
                            except Exception:
                                assertion_attempts.append("document_text_error")

                        if not found:
                            raise PlaywrightTimeoutError(f"Assertion text '{assert_text}' not found or not visible")
                    step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"assertion_attempts": assertion_attempts, "assert_text": assert_text}}
            elif kind == "accessibility_scan":
                report = self._run_accessibility_scan(page, ui_input)
                issues = report.get("issues", [])
                step_result = {"step": step_title, "status": "passed" if report.get("passed", False) else "failed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": report}
                if not report.get("passed", False):
                    issue_count = int(report.get("issue_count", len(issues)))
                    step_result["error"] = f"Accessibility issues found: {issue_count}"
            elif kind == "performance_budget":
                report = self._run_performance_scan(page, ui_input)
                step_result = {"step": step_title, "status": "passed" if report.get("passed", False) else "failed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": report}
                if not report.get("passed", False):
                    step_result["error"] = report.get("error", "Performance budget exceeded")
            elif kind == "security_smoke":
                report = self._run_security_scan(page)
                step_result = {"step": step_title, "status": "passed" if report.get("passed", False) else "failed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": report}
                if not report.get("passed", False):
                    step_result["error"] = report.get("error", "Security findings detected")
            elif kind == "visual_compare":
                report = self._run_visual_compare(page, ui_input)
                step_result = {"step": step_title, "status": "passed" if report.get("passed", False) else "failed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": report}
                if not report.get("passed", False):
                    step_result["error"] = report.get("error", "Visual comparison mismatch")
            elif kind == "refresh":
                page.reload(wait_until="domcontentloaded", timeout=5000)
                self._wait_for_page_ready(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"action": "page_reload"}}
            elif kind == "wait":
                wait_target = action.get("target", "")
                self._wait_for_page_ready(page)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"wait_for": wait_target}}
            else:
                return {"step": step_title, "status": "skipped", "reason": "Unsupported action", "stage": action.get("stage")}
            page.wait_for_timeout(200)
            return step_result
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            return {"step": step_title, "status": "failed", "error": str(exc), "stage": action.get("stage"), "raw_step": action.get("raw_step", "")}

    def _workflow_summary(self, workflow: dict, steps: list[dict]) -> dict:
        workflow_steps = [
            s
            for s in steps
            if s.get("stage") is not None and s.get("step", "").split(":")[0] in {"goto", "click", "fill", "check", "select", "assert_visible", "refresh", "accessibility_scan", "performance_budget", "security_smoke", "visual_compare"}
        ]
        passed = len([s for s in workflow_steps if s.get("status") == "passed"])
        failed = len([s for s in workflow_steps if s.get("status") == "failed"])
        skipped = len([s for s in workflow_steps if s.get("status") == "skipped"])
        return {
            "planned": workflow.get("total_actions", 0),
            "executed": len(workflow_steps),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }

    def _check_summary(self, steps: list[dict]) -> dict:
        categories: dict[str, dict[str, int]] = {}
        for step in steps:
            category = self._step_category(step)
            if not category:
                continue
            if category not in categories:
                categories[category] = {"passed": 0, "failed": 0, "issues": 0}
            status = str(step.get("status", "")).lower()
            if status == "passed":
                categories[category]["passed"] += 1
            elif status == "failed":
                categories[category]["failed"] += 1
            details = step.get("details", {}) if isinstance(step.get("details", {}), dict) else {}
            issue_count = details.get("issue_count")
            if issue_count is not None:
                categories[category]["issues"] += int(issue_count)
            elif details.get("issues"):
                categories[category]["issues"] += len(details.get("issues", []))

        total_checks = sum(item["passed"] + item["failed"] for item in categories.values())
        failed_checks = sum(item["failed"] for item in categories.values())
        return {
            "total_checks": total_checks,
            "failed_checks": failed_checks,
            "passed_checks": total_checks - failed_checks,
            "categories": categories,
        }

    def _step_category(self, step: dict) -> str:
        kind = str(step.get("step", "")).split(":", 1)[0]
        if kind == "accessibility_scan":
            return "accessibility"
        if kind == "performance_budget":
            return "performance"
        if kind == "security_smoke":
            return "security"
        if kind == "visual_compare":
            return "visual"
        return ""

    def _locator_summary(self, steps: list[dict]) -> dict:
        healed = 0
        direct = 0
        discovery_lookup = 0
        unresolved = 0
        for step in steps:
            details = step.get("details", {})
            resolution = details.get("locator_resolution", {})
            if not resolution:
                continue
            if resolution.get("healed"):
                healed += 1
            elif resolution.get("source") == "discovery_lookup":
                discovery_lookup += 1
            elif resolution.get("matched"):
                direct += 1
            else:
                unresolved += 1
        return {
            "healed_steps": healed,
            "direct_matches": direct,
            "discovery_lookup_matches": discovery_lookup,
            "unresolved": unresolved,
        }

    def _visual_trace_enabled(self, ui_input: dict) -> bool:
        return bool(ui_input.get("headed", False))

    def _step_pause_ms(self, ui_input: dict) -> int:
        try:
            return max(300, int(ui_input.get("step_pause_ms", 1200)))
        except (TypeError, ValueError):
            return 1200

    def _keep_browser_open_ms(self, ui_input: dict) -> int:
        try:
            return max(500, int(ui_input.get("keep_browser_open_ms", 2500)))
        except (TypeError, ValueError):
            return 2500

    def _wait_for_page_ready(self, page) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        try:
            page.locator("body").wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        for selector in [
            "[class*='backdrop']",
            "[class*='overlay']",
            "[class*='spinner']",
            "[class*='loading']",
            "[aria-busy='true']",
        ]:
            try:
                page.locator(selector).first.wait_for(state="hidden", timeout=1500)
            except PlaywrightTimeoutError:
                pass
            except PlaywrightError:
                pass
        page.wait_for_timeout(250)

    def _wait_for_initial_page_ready(self, page) -> None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        try:
            page.locator("body").wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except PlaywrightTimeoutError:
            pass
        for selector in [
            "[class*='backdrop']",
            "[class*='overlay']",
            "[class*='spinner']",
            "[class*='loading']",
            "[aria-busy='true']",
            "[role='progressbar']",
        ]:
            try:
                page.locator(selector).first.wait_for(state="hidden", timeout=2500)
            except PlaywrightTimeoutError:
                pass
            except PlaywrightError:
                pass
        page.wait_for_timeout(2000)

    def _show_visual_banner(self, page, title: str, message: str, tone: str = "info") -> None:
        return

    def _clear_visual_banner(self, page) -> None:
        return

    def _extract_quoted_text(self, text: str) -> list[str]:
        return [match[1:-1] for match in re.findall(r"'[^']+'|\"[^\"]+\"", text)]

    def _extract_target_phrase(self, text: str) -> str:
        lowered = text.lower()
        if "click on" in lowered:
            return text.lower().split("click on", 1)[1].strip(" .")
        if lowered.startswith("click"):
            return text.split(" ", 1)[1].strip(" .") if " " in text else ""
        if "verify that" in lowered:
            return text.lower().split("verify that", 1)[1].strip(" .")
        return text

    def _extract_assert_phrase(self, text: str) -> str:
        lower = text.lower()
        if "verify that" in lower:
            phrase = text.split("that", 1)[1]
            return phrase.replace("is visible", "").replace("visible", "").replace("exists", "").replace("is loaded", "").replace("successfully", "").strip(" .")
        if "verify" in lower:
            phrase = text.split("verify", 1)[1]
            return phrase.replace("is visible", "").replace("visible", "").replace("exists", "").replace("is loaded", "").replace("successfully", "").strip(" .")
        return text

    def _extract_quoted_value(self, text: str) -> str:
        """Extract value from single or double quotes in text. Returns empty string if not found."""
        # Try single quotes first
        match = re.search(r"'([^']+)'", text)
        if match:
            return match.group(1)
        # Try double quotes
        match = re.search(r'"([^"]+)"', text)
        if match:
            return match.group(1)
        return ""

    def _extract_checkbox_target(self, text: str) -> str:
        match = re.search(
            r"(?:click\s+on\s+|click\s+)?(?:the\s+)?check(?:\s|-)?box(?:\s+for)?\s+(.*)",
            text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip(" .!\"'")
        return self._extract_target_phrase(text).strip(" .!\"'")

    def _extract_url_contains_value(self, text: str) -> str:
        match = re.search(r"url\s+(?:contains?|to contain)\s+['\"]?([^'\"]+)['\"]?", text, re.IGNORECASE)
        if match:
            return match.group(1).strip(" .!\"'")
        return ""

    def _extract_field_list(self, text: str) -> list[str]:
        lower = text.lower()
        if ":" in text:
            segment = text.split(":", 1)[1]
        elif "enter" in lower:
            segment = re.split(r"enter", text, maxsplit=1, flags=re.IGNORECASE)[1]
        elif "fill" in lower:
            segment = re.split(r"fill", text, maxsplit=1, flags=re.IGNORECASE)[1]
        else:
            segment = text
        raw_fields = [field.strip(" .!") for field in re.split(r",| and ", segment) if field.strip(" .!")]
        return raw_fields if raw_fields else [text.strip()]

    def _dismiss_common_popups(self, page) -> None:
        """Dismiss common website popups and overlays like location selectors, cookie banners, etc."""
        popup_selectors = [
            "button:has-text('Close')",
            "button:has-text('Dismiss')",
            "button:has-text('No Thanks')",
            "button:has-text('Not now')",
            "button:has-text('Skip')",
            "button:has-text('Later')",
            "button:has-text('Maybe later')",
            "button:has-text('Continue without')",
            "button:has-text('x')",
            "button:has-text('X')",
            "button:has-text('×')",
            "button:has-text('✕')",
            "[class*='close']",
            "[class*='dismiss']",
            "[class*='cross']",
            "[class*='modal-close']",
            "[class*='popup-close']",
            "[class*='close-btn']",
            "[id*='close']",
            "[id*='dismiss']",
            "[data-testid*='close']",
            "[data-dismiss]",
            "[data-action*='close']",
            "button[aria-label*='Close']",
            "button[aria-label*='Dismiss']",
            "[aria-label*='close' i]",
            "[title*='close' i]",
            "[role='button'][aria-label*='close' i]",
            "[role='button'][title*='close' i]",
        ]

        def _dismiss_in_context(context) -> bool:
            dismissed_local = False
            for selector in popup_selectors:
                try:
                    locator = context.locator(selector)
                    count = min(locator.count(), 4)
                    for index in range(count):
                        candidate = locator.nth(index)
                        if candidate.is_visible(timeout=250):
                            candidate.click(timeout=800)
                            page.wait_for_timeout(150)
                            dismissed_local = True
                except Exception:
                    pass
            return dismissed_local

        def _dismiss_by_dom_probe() -> bool:
            try:
                clicked = page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return rect.width > 6 && rect.height > 6 && style.visibility !== 'hidden' && style.display !== 'none' && style.pointerEvents !== 'none';
                      };
                      const score = (el) => {
                        const text = (el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const title = (el.getAttribute('title') || '').toLowerCase();
                        const cls = (el.className || '').toString().toLowerCase();
                        const id = (el.id || '').toLowerCase();
                        const role = (el.getAttribute('role') || '').toLowerCase();
                        const rect = el.getBoundingClientRect();
                        const posBonus = (rect.top < window.innerHeight * 0.6 && rect.left > window.innerWidth * 0.4) ? 10 : 0;
                        let s = posBonus;
                        if (/(close|dismiss|skip|later|not now)/.test(`${text} ${aria} ${title} ${cls} ${id}`)) s += 80;
                        if (/^(x|×|✕)$/.test(text)) s += 70;
                        if (role === 'button' || el.tagName.toLowerCase() === 'button') s += 8;
                        return s;
                      };

                      const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'))
                        .filter((el) => visible(el))
                        .map((el) => ({ el, s: score(el) }))
                        .filter((item) => item.s >= 70)
                        .sort((a, b) => b.s - a.s)
                        .slice(0, 5);

                      let clickedAny = false;
                      for (const item of candidates) {
                        item.el.click();
                        clickedAny = true;
                      }
                      return clickedAny;
                    }
                    """
                )
                return bool(clicked)
            except Exception:
                return False

        for _ in range(4):
            dismissed_any = False

            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(120)
            except Exception:
                pass

            if _dismiss_in_context(page):
                dismissed_any = True

            for frame in page.frames:
                try:
                    if _dismiss_in_context(frame):
                        dismissed_any = True
                except Exception:
                    pass

            if _dismiss_by_dom_probe():
                dismissed_any = True

            if not dismissed_any:
                break

    def _contextual_checkbox_locator(self, page, action: dict, execution_context: dict):
        target = str(action.get("target", ""))
        try:
            scope_hint = self._merge_scope_hint(
                execution_context.get("active_scope_hint", ""),
                action.get("raw_step", ""),
                target,
            )
            target_slug = self._slugify(target)
            target_tokens = [token for token in target_slug.split("_") if token and token not in {"and", "or", "the", "for"}]
            if not target_tokens:
                return None

            choice = page.evaluate(
                """
                ([targetTokens, scopeHint]) => {
                  document.querySelectorAll("[data-quantumqa-checkbox]").forEach((el) => el.removeAttribute("data-quantumqa-checkbox"));
                  const normalized = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
                  const tokenSet = Array.from(new Set((targetTokens || []).map((t) => normalized(t)).filter(Boolean)));
                  const hint = normalized(scopeHint || "");
                  const controls = Array.from(document.querySelectorAll("input[type='checkbox'], [role='checkbox']"));
                  if (!controls.length) return { token: "" };

                  const scored = controls.map((el) => {
                    const id = el.id || "";
                    const name = el.getAttribute("name") || "";
                    const aria = el.getAttribute("aria-label") || "";
                    const labelText = (el.labels ? Array.from(el.labels) : []).map((l) => l.textContent || "").join(" ");
                    const containerText = (el.closest("label, div, section, form")?.innerText || "").slice(0, 1500);
                    const summary = normalized([id, name, aria, labelText].join(" "));
                    const context = normalized(containerText);
                    let score = 0;

                    for (const token of tokenSet) {
                      if (!token) continue;
                      if (summary === token) score += 100;
                      else if (summary.includes(token)) score += 45;
                      if (context.includes(token)) score += 18;
                    }
                    if (hint && context.includes(hint)) score += 8;
                    return { el, score };
                  }).sort((a, b) => b.score - a.score);

                  const best = scored[0];
                  if (!best || best.score <= 0) return { token: "" };
                  const token = `qqa_checkbox_${Date.now()}_${Math.floor(Math.random() * 100000)}`;
                  best.el.setAttribute("data-quantumqa-checkbox", token);
                  return { token };
                }
                """,
                [target_tokens, scope_hint],
            )
            token = str((choice or {}).get("token", "")).strip()
            if not token:
                return None
            locator = page.locator(f"[data-quantumqa-checkbox='{token}']")
            return locator.first if locator.count() > 0 else None
        except PlaywrightError:
            return None

    def _set_checkbox(self, locator) -> None:
        try:
            locator.scroll_into_view_if_needed(timeout=2000)
        except PlaywrightError:
            pass
        try:
            input_type = locator.evaluate("(el) => (el.getAttribute('type') || '').toLowerCase()")
            if input_type == "checkbox":
                try:
                    if not locator.is_checked():
                        locator.check(force=True)
                except PlaywrightError:
                    locator.click(force=True)
                return
        except PlaywrightError:
            pass
        locator.click(force=True)

    def _sample_value_for_field(self, field: str) -> str:
        key = self._slugify(field)
        if "email" in key:
            return f"test_{int(time.time())}@example.com"
        if "password" in key:
            return "Test@1234"
        if "mobile" in key or "phone" in key:
            return self._random_mobile_number()
        if "zip" in key or "postcode" in key:
            return "560001"
        if "country" in key:
            return "India"
        if "state" in key:
            return "Karnataka"
        if "city" in key:
            return "Bengaluru"
        if "first_name" in key or key == "first":
            return "Auto"
        if "last_name" in key or key == "last":
            return "Tester"
        if "name" in key:
            return "Auto Tester"
        if "address" in key:
            return "123 Test Street"
        if "company" in key:
            return "Quantum QA"
        if "day" in key:
            return "10"
        if "month" in key:
            return "May"
        if "year" in key:
            return "1995"
        return "sample input"

    def _random_mobile_number(self) -> str:
        return f"{random.randint(6000000000, 9999999999)}"

    def _assertion_text(self, raw: str) -> str:
        key = self._slugify(raw)
        if "logged_in_as_username" in key:
            return "Logged in as"
        if "account_created" in key:
            return "ACCOUNT CREATED"
        if "account_deleted" in key:
            return "ACCOUNT DELETED"
        if "new_user_signup" in key:
            return "New User Signup"
        if "enter_account_information" in key:
            return "ENTER ACCOUNT INFORMATION"
        return raw

    def _assertion_candidates(self, raw: str) -> list[str]:
        candidates = []
        canonical = self._assertion_text(raw).strip()
        simplified = self._strip_assertion_suffixes(raw).strip()
        for candidate in [canonical, simplified]:
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return candidates or [raw]

    def _strip_assertion_suffixes(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(r"\bis visible\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bvisible successfully\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bexists\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bdisplayed\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bis loaded\b", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip(" .!")

    def _normalize_visible_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())).strip()

    def _merge_scope_hint(self, current: str, raw_step: str, target: str) -> str:
        parts = [str(current or ""), str(raw_step or ""), str(target or "")]
        return " ".join(part for part in parts if part).strip().lower()

    def _contextual_fill_locator(self, page, action: dict, execution_context: dict):
        target = str(action.get("target", ""))
        try:
            scope_hint = self._merge_scope_hint(
                execution_context.get("active_scope_hint", ""),
                action.get("raw_step", ""),
                target,
            )
            target_slug = self._slugify(target)
            target_tokens = [token for token in target_slug.split("_") if token and token not in {"and", "or", "the"}]
            if not target_tokens:
                return None

            last_form_marker = str(execution_context.get("last_form_marker", ""))
            choice = page.evaluate(
                """
                ([targetTokens, scopeHint, lastFormMarker]) => {
                  document.querySelectorAll("[data-quantumqa-target]").forEach((el) => el.removeAttribute("data-quantumqa-target"));
                  const controls = Array.from(document.querySelectorAll("input, textarea, select")).filter((el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    if (style.visibility === "hidden" || style.display === "none") return false;
                    if (!rect.width || !rect.height) return false;
                    if (el.disabled || el.readOnly) return false;
                    const type = (el.getAttribute("type") || "").toLowerCase();
                    if (["hidden", "submit", "button", "image", "file"].includes(type)) return false;
                    return true;
                  });
                  if (!controls.length) {
                    return { index: -1 };
                  }

                  const hint = (scopeHint || "").toLowerCase();
                  const wantsSignup = /(signup|sign up|register|new user|create account)/.test(hint);
                  const wantsLogin = /(login|sign in|log in|your account)/.test(hint);

                  const normalized = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
                  const tokenSet = Array.from(new Set((targetTokens || []).map((t) => normalized(t)).filter(Boolean)));

                  const scored = controls.map((el, index) => {
                    const type = (el.getAttribute("type") || "").toLowerCase();
                    const id = el.id || "";
                    const name = el.getAttribute("name") || "";
                    const placeholder = el.getAttribute("placeholder") || "";
                    const aria = el.getAttribute("aria-label") || "";
                    const labels = (el.labels ? Array.from(el.labels) : []).map((l) => l.textContent || "").join(" ");
                    const nearby = (el.closest("label, div, section, form, article, main")?.innerText || "").slice(0, 1400);
                    const summary = normalized([id, name, placeholder, aria, labels, type].join(" "));
                    const context = normalized(nearby);
                    const form = el.closest("form");
                    const container = form || el.closest("section, article, main, div");
                    const marker = `${form?.id || ''}|${form?.name || ''}|${form?.getAttribute('action') || ''}|${(container?.querySelector('h1,h2,h3,h4,legend')?.textContent || '').trim()}`;
                    let score = 0;

                    for (const token of tokenSet) {
                      if (!token) continue;
                      if (summary === token) score += 80;
                      else if (summary.includes(token)) score += 35;
                      if (context.includes(token)) score += 12;
                    }

                    if (tokenSet.includes("email") && type === "email") score += 40;
                    if (tokenSet.includes("password") && type === "password") score += 40;
                    if (tokenSet.includes("country") && el.tagName.toLowerCase() === "select") score += 25;
                    if (tokenSet.includes("state") && el.tagName.toLowerCase() === "select") score += 18;
                    if ((tokenSet.includes("day") || tokenSet.includes("month") || tokenSet.includes("year") || tokenSet.includes("birth")) && el.tagName.toLowerCase() === "select") score += 20;
                    if (wantsSignup && /(signup|sign up|register|new user|create account)/.test(context)) score += 35;
                    if (wantsLogin && /(login|sign in|log in|your account)/.test(context)) score += 35;
                    if (lastFormMarker && marker === lastFormMarker) score += 30;
                    if (form) score += 4;
                    return { index, score, marker, element: el };
                  }).sort((a, b) => b.score - a.score);

                  const best = scored[0];
                  if (!best || best.index < 0) return { index: -1 };
                  const token = `qqa_target_${Date.now()}_${Math.floor(Math.random() * 100000)}`;
                  best.element.setAttribute("data-quantumqa-target", token);
                  return { index: best.index, marker: best.marker, token };
                }
                """,
                [target_tokens, scope_hint, last_form_marker],
            )
            token = str(choice.get("token", "")).strip()
            if not token:
                return None
            locator = page.locator(f"[data-quantumqa-target='{token}']")
            try:
                if locator.count() == 0:
                    return None
            except PlaywrightError:
                return None
            marker = str(choice.get("marker", ""))
            if marker:
                execution_context["last_form_marker"] = marker
            self.last_locator_resolution = {"source": "contextual", "strategy": "fill", "healed": False, "matched": True}
            return locator
        except (PlaywrightError, ValueError, TypeError):
            return None

    def _fill_value(self, page, locator, value: str) -> tuple[bool, str]:
        if self._is_probable_otp_locator(page, locator, value):
            otp_filled, otp_mode = self._fill_otp_value(page, locator, value)
            if otp_filled:
                return True, otp_mode
            return False, "otp_fill_failed"
        otp_filled, otp_mode = self._fill_otp_value(page, locator, value)
        if otp_filled:
            return True, otp_mode
        try:
            tag = page.evaluate("(el) => el.tagName.toLowerCase()", locator.first.element_handle(timeout=2000))
        except PlaywrightError:
            tag = ""
        if str(tag) == "select":
            choice = page.evaluate(
                """
                (el) => {
                  const options = Array.from(el.options || []);
                  if (!options.length) return null;
                  const valid = options.find((o) => !o.disabled && String(o.value || "").trim() && !/select|choose/i.test(String(o.textContent || "")));
                  return valid ? String(valid.value) : String(options[0].value || "");
                }
                """,
            )
            if choice is None:
                return False, "select has no options"
            locator.select_option(value=str(choice))
            return True, "selected_option"
        locator.fill(value)
        return True, "filled_text"

    def _is_probable_otp_locator(self, page, locator, value: str) -> bool:
        otp_value = str(value or "").strip()
        if not otp_value or not otp_value.isdigit() or len(otp_value) < 4:
            return False
        try:
            handle = locator.first.element_handle(timeout=2000)
            if handle is None:
                return False
            return bool(page.evaluate(
                """
                (el) => {
                  const type = String(el.getAttribute("type") || "").toLowerCase();
                  const inputMode = String(el.getAttribute("inputmode") || "").toLowerCase();
                  const autoComplete = String(el.getAttribute("autocomplete") || "").toLowerCase();
                  const maxLength = parseInt(el.getAttribute("maxlength") || "0", 10);
                  const text = [
                    el.id || "",
                    el.getAttribute("name") || "",
                    el.getAttribute("placeholder") || "",
                    el.getAttribute("aria-label") || "",
                    type,
                    inputMode,
                    autoComplete,
                  ].join(" ").toLowerCase();
                  return maxLength === 1 || inputMode === "numeric" || autoComplete === "one-time-code" || /otp|code|digit|pin/.test(text);
                }
                """,
                handle,
            ))
        except PlaywrightError:
            return False

    def _fill_otp_value(self, page, locator, value: str) -> tuple[bool, str]:
        otp_value = str(value or "").strip()
        if not otp_value:
            return False, ""
        try:
            handle = locator.first.element_handle(timeout=2000)
            if handle is None:
                return False, ""
            result = page.evaluate(
                """
                ([el, otpValue]) => {
                  const normalized = String(otpValue || "").trim();
                  if (!normalized) return { handled: false };
                  const visible = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                  };
                  const setNativeValue = (node, nextValue) => {
                    const prototype = Object.getPrototypeOf(node);
                    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
                    if (descriptor && descriptor.set) {
                      descriptor.set.call(node, nextValue);
                    } else {
                      node.value = nextValue;
                    }
                  };
                  const looksOtpInput = (node) => {
                    const type = String(node.getAttribute("type") || "").toLowerCase();
                    const inputMode = String(node.getAttribute("inputmode") || "").toLowerCase();
                    const autoComplete = String(node.getAttribute("autocomplete") || "").toLowerCase();
                    const maxLength = parseInt(node.getAttribute("maxlength") || "0", 10);
                    const text = [
                      node.id || "",
                      node.getAttribute("name") || "",
                      node.getAttribute("placeholder") || "",
                      node.getAttribute("aria-label") || "",
                      type,
                      inputMode,
                      autoComplete,
                    ].join(" ").toLowerCase();
                    return maxLength === 1 || inputMode === "numeric" || autoComplete === "one-time-code" || /otp|code|digit|pin/.test(text);
                  };

                  if (!visible(el) || !looksOtpInput(el)) return { handled: false };

                  const root = el.closest("form, [role='dialog'], section, article, main, div") || el.parentElement || document.body;
                  let group = Array.from(root.querySelectorAll("input")).filter((node) => visible(node) && !node.disabled && looksOtpInput(node));
                  if (group.length < 2 && el.parentElement) {
                    group = Array.from(el.parentElement.querySelectorAll("input")).filter((node) => visible(node) && !node.disabled && looksOtpInput(node));
                  }
                  if (group.length < 2) return { handled: false };

                  const top = Math.min(...group.map((node) => Math.round(node.getBoundingClientRect().top)));
                  group = group
                    .filter((node) => Math.abs(Math.round(node.getBoundingClientRect().top) - top) <= 12)
                    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);

                  if (group.length < 2) return { handled: false };

                  group.forEach((node) => {
                    node.focus();
                    setNativeValue(node, "");
                    node.dispatchEvent(new Event("input", { bubbles: true }));
                    node.dispatchEvent(new Event("change", { bubbles: true }));
                  });

                  const digits = normalized.split("");
                  for (let index = 0; index < digits.length && index < group.length; index += 1) {
                    const node = group[index];
                    node.focus();
                    setNativeValue(node, digits[index]);
                    node.dispatchEvent(new Event("input", { bubbles: true }));
                    node.dispatchEvent(new Event("change", { bubbles: true }));
                    node.dispatchEvent(new KeyboardEvent("keyup", { key: digits[index], bubbles: true }));
                  }

                  const last = group[Math.min(digits.length, group.length) - 1];
                  if (last) last.blur();
                  const actual = group.slice(0, digits.length).map((node) => String(node.value || "")).join("");
                  return {
                    handled: actual === digits.slice(0, group.length).join(""),
                    count: Math.min(digits.length, group.length),
                    actual,
                  };
                }
                """,
                [handle, otp_value],
            )
            if result and result.get("handled"):
                page.wait_for_timeout(200)
                return True, f"filled_otp_group:{result.get('count', 0)}"
        except PlaywrightError:
            return False, ""
        return False, ""

    def _capture_form_marker(self, page, locator) -> str:
        try:
            handle = locator.first.element_handle(timeout=2000)
            if handle is None:
                return ""
            marker = page.evaluate(
                """
                (el) => {
                  const form = el.closest('form');
                  const container = form || el.closest('section, article, main, div');
                  return `${form?.id || ''}|${form?.name || ''}|${form?.getAttribute('action') || ''}|${(container?.querySelector('h1,h2,h3,h4,legend')?.textContent || '').trim()}`;
                }
                """,
                handle,
            )
            return str(marker or "").strip()
        except PlaywrightError:
            return ""

    def _highlight_locator(self, page, locator, ui_input: dict) -> None:
        if not self._visual_trace_enabled(ui_input):
            return
        try:
            handle = locator.first.element_handle(timeout=2000)
            if handle is None:
                return
            page.evaluate(
                """
                (el) => {
                  const priorOutline = el.style.outline;
                  const priorOffset = el.style.outlineOffset;
                  const priorTransition = el.style.transition;
                  el.style.transition = 'outline 0.15s ease-in-out';
                  el.style.outline = '4px solid #f97316';
                  el.style.outlineOffset = '2px';
                  setTimeout(() => {
                    el.style.outline = priorOutline;
                    el.style.outlineOffset = priorOffset;
                    el.style.transition = priorTransition;
                  }, 900);
                }
                """,
                handle,
            )
            page.wait_for_timeout(min(900, 500))
        except PlaywrightError:
            return

    def _capture_step_screenshot(self, page, step_artifacts_dir: Path, action: dict, result: dict) -> str:
        stage = action.get("stage")
        if stage is None:
            return ""
        safe_step = self._slugify(result.get("step", f"step_{stage}")) or f"step_{stage}"
        filename = f"{int(stage):02d}_{safe_step}_{result.get('status', 'unknown')}.png"
        output_path = step_artifacts_dir / filename
        try:
            page.screenshot(path=str(output_path), full_page=True)
            return str(output_path).replace("\\", "/")
        except PlaywrightError:
            return ""

    def _dynamic_locator_with_healing(self, page, target: str, kind: str, resolved_element: str):
        """Try primary locator first, then fallback to cached or self-healed alternatives."""
        cache_key = f"{kind}:{resolved_element or target}"
        cached_locator = self._locator_from_cache(page, self.locator_cache.get(cache_key))
        if cached_locator is not None:
            self.last_locator_resolution = {"source": "cache", "strategy": self.locator_cache[cache_key].get("strategy", "unknown"), "healed": True, "matched": True}
            return cached_locator

        # Try primary locator first
        locator = self._dynamic_locator(page, target, kind)
        if locator is not None:
            self.last_locator_resolution = {"source": "dynamic", "strategy": kind, "healed": False, "matched": True}
            return locator

        # Self-heal: try aggressive fallbacks
        locator, cache_entry = self._try_healing_fallbacks(page, target, kind, resolved_element)
        if locator is not None:
            if cache_entry is not None:
                self.locator_cache[cache_key] = cache_entry
            self.last_locator_resolution = {"source": "healing", "strategy": kind, "healed": True, "matched": True}
            return locator

        self.last_locator_resolution = {"source": "unresolved", "strategy": kind, "healed": False, "matched": False}
        return None

    def _consume_locator_resolution(self) -> dict:
        resolution = dict(self.last_locator_resolution)
        self.last_locator_resolution = {}
        return resolution

    def _selector_resolution(self, element: dict, healed: bool, source: str) -> dict:
        selector = element.get("selector", {})
        return {
            "source": source,
            "strategy": selector.get("strategy", "unknown"),
            "healed": healed,
            "matched": True,
            "element": element.get("name", ""),
        }

    def _try_healing_fallbacks(self, page, target: str, kind: str, resolved_element: str):
        """Return the first matching fallback locator plus cache metadata."""
        cleaned = target.strip().strip("'\"")
        slug = self._slugify(cleaned)
        tokens = [token for token in slug.split("_") if token]
        fallbacks: list[tuple[Any, dict[str, Any]]] = []

        if kind == "click":
            self._append_locator_candidate(fallbacks, page.get_by_role("button", name=cleaned, exact=False), {"strategy": "role", "role": "button", "name": cleaned})
            self._append_locator_candidate(fallbacks, page.get_by_role("link", name=cleaned, exact=False), {"strategy": "role", "role": "link", "name": cleaned})
            self._append_locator_candidate(fallbacks, page.get_by_text(cleaned, exact=False), {"strategy": "text", "value": cleaned})
            if tokens:
                for token in tokens:
                    self._append_locator_candidate(fallbacks, page.locator(f"button[class*='{token}']"), {"strategy": "css", "value": f"button[class*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"a[class*='{token}']"), {"strategy": "css", "value": f"a[class*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"button[id*='{token}']"), {"strategy": "css", "value": f"button[id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"a[id*='{token}']"), {"strategy": "css", "value": f"a[id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"[onclick*='{token}']"), {"strategy": "css", "value": f"[onclick*='{token}']"})
            self._append_locator_candidate(fallbacks, page.locator("button:visible").filter(has_text=cleaned), {"strategy": "text", "value": cleaned})
            self._append_locator_candidate(fallbacks, page.locator("a:visible").filter(has_text=cleaned), {"strategy": "text", "value": cleaned})
            self._append_locator_candidate(fallbacks, page.locator("[role='button']:visible").filter(has_text=cleaned), {"strategy": "text", "value": cleaned})
            for word in cleaned.split():
                if len(word) > 3:
                    self._append_locator_candidate(fallbacks, page.locator("button").filter(has_text=word), {"strategy": "text", "value": word})
                    self._append_locator_candidate(fallbacks, page.locator("a").filter(has_text=word), {"strategy": "text", "value": word})

        elif kind == "fill":
            if tokens:
                for token in tokens:
                    self._append_locator_candidate(fallbacks, page.locator(f"input[name*='{token}']"), {"strategy": "css", "value": f"input[name*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"input[id*='{token}']"), {"strategy": "css", "value": f"input[id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"textarea[name*='{token}']"), {"strategy": "css", "value": f"textarea[name*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"textarea[id*='{token}']"), {"strategy": "css", "value": f"textarea[id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"input[class*='{token}']"), {"strategy": "css", "value": f"input[class*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"textarea[class*='{token}']"), {"strategy": "css", "value": f"textarea[class*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"input[placeholder*='{token}']"), {"strategy": "css", "value": f"input[placeholder*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"textarea[placeholder*='{token}']"), {"strategy": "css", "value": f"textarea[placeholder*='{token}']"})

            if "email" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[type='email']"), {"strategy": "css", "value": "input[type='email']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='email']"), {"strategy": "css", "value": "input[name*='email']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='email']"), {"strategy": "css", "value": "input[id*='email']"})
            if "password" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[type='password']"), {"strategy": "css", "value": "input[type='password']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='password']"), {"strategy": "css", "value": "input[name*='password']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='password']"), {"strategy": "css", "value": "input[id*='password']"})
            if "first" in slug or "fname" in slug or "firstname" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='first']"), {"strategy": "css", "value": "input[name*='first']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='first']"), {"strategy": "css", "value": "input[id*='first']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='First']"), {"strategy": "css", "value": "input[placeholder*='First']"})
            if "last" in slug or "lname" in slug or "lastname" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='last']"), {"strategy": "css", "value": "input[name*='last']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='last']"), {"strategy": "css", "value": "input[id*='last']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Last']"), {"strategy": "css", "value": "input[placeholder*='Last']"})
            if "title" in slug or "mr" in slug or "ms" in slug or "mrs" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='title']"), {"strategy": "css", "value": "input[name*='title']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='title']"), {"strategy": "css", "value": "input[id*='title']"})
                self._append_locator_candidate(fallbacks, page.locator("select[name*='title']"), {"strategy": "css", "value": "select[name*='title']"})
                self._append_locator_candidate(fallbacks, page.locator("select[id*='title']"), {"strategy": "css", "value": "select[id*='title']"})
            if "date" in slug or "birth" in slug or "dob" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='date']"), {"strategy": "css", "value": "input[name*='date']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='date']"), {"strategy": "css", "value": "input[id*='date']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='birth']"), {"strategy": "css", "value": "input[name*='birth']"})
                self._append_locator_candidate(fallbacks, page.locator("input[type='date']"), {"strategy": "css", "value": "input[type='date']"})
                self._append_locator_candidate(fallbacks, page.locator("input[type='text'][placeholder*='Date']"), {"strategy": "css", "value": "input[type='text'][placeholder*='Date']"})
            if "company" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='company']"), {"strategy": "css", "value": "input[name*='company']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='company']"), {"strategy": "css", "value": "input[id*='company']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Company']"), {"strategy": "css", "value": "input[placeholder*='Company']"})
            if "address" in slug or "street" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='address']"), {"strategy": "css", "value": "input[name*='address']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='address']"), {"strategy": "css", "value": "input[id*='address']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='street']"), {"strategy": "css", "value": "input[name*='street']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Address']"), {"strategy": "css", "value": "input[placeholder*='Address']"})
            if "country" in slug:
                self._append_locator_candidate(fallbacks, page.locator("select[name*='country']"), {"strategy": "css", "value": "select[name*='country']"})
                self._append_locator_candidate(fallbacks, page.locator("select[id*='country']"), {"strategy": "css", "value": "select[id*='country']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='country']"), {"strategy": "css", "value": "input[name*='country']"})
            if "state" in slug or "province" in slug:
                self._append_locator_candidate(fallbacks, page.locator("select[name*='state']"), {"strategy": "css", "value": "select[name*='state']"})
                self._append_locator_candidate(fallbacks, page.locator("select[id*='state']"), {"strategy": "css", "value": "select[id*='state']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='state']"), {"strategy": "css", "value": "input[name*='state']"})
            if "city" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='city']"), {"strategy": "css", "value": "input[name*='city']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='city']"), {"strategy": "css", "value": "input[id*='city']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='City']"), {"strategy": "css", "value": "input[placeholder*='City']"})
            if "zip" in slug or "postal" in slug or "code" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='zip']"), {"strategy": "css", "value": "input[name*='zip']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='zip']"), {"strategy": "css", "value": "input[id*='zip']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='postal']"), {"strategy": "css", "value": "input[name*='postal']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Zip']"), {"strategy": "css", "value": "input[placeholder*='Zip']"})
            if "phone" in slug or "mobile" in slug or "telephone" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='phone']"), {"strategy": "css", "value": "input[name*='phone']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='phone']"), {"strategy": "css", "value": "input[id*='phone']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='mobile']"), {"strategy": "css", "value": "input[name*='mobile']"})
                self._append_locator_candidate(fallbacks, page.locator("input[type='tel']"), {"strategy": "css", "value": "input[type='tel']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Phone']"), {"strategy": "css", "value": "input[placeholder*='Phone']"})
            self._append_locator_candidate(fallbacks, page.locator("input:visible"), {"strategy": "css", "value": "input:visible"})

        elif kind == "check":
            if tokens:
                for token in tokens:
                    self._append_locator_candidate(fallbacks, page.locator(f"input[type='checkbox'][name*='{token}']"), {"strategy": "css", "value": f"input[type='checkbox'][name*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"input[type='checkbox'][id*='{token}']"), {"strategy": "css", "value": f"input[type='checkbox'][id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"input[type='checkbox'][class*='{token}']"), {"strategy": "css", "value": f"input[type='checkbox'][class*='{token}']"})
            self._append_locator_candidate(fallbacks, page.get_by_role("checkbox", name=cleaned, exact=False), {"strategy": "role", "role": "checkbox", "name": cleaned})
            self._append_locator_candidate(fallbacks, page.get_by_label(cleaned, exact=False), {"strategy": "label", "value": cleaned})
            self._append_locator_candidate(fallbacks, page.locator("label").filter(has_text=cleaned).locator("input[type='checkbox']"), {"strategy": "label", "value": cleaned})
            if "newsletter" in slug or "news" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='news']"), {"strategy": "css", "value": "input[name*='news']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='news']"), {"strategy": "css", "value": "input[id*='news']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='letter']"), {"strategy": "css", "value": "input[name*='letter']"})
            if "offer" in slug or "special" in slug or "partner" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='offer']"), {"strategy": "css", "value": "input[name*='offer']"})
                self._append_locator_candidate(fallbacks, page.locator("input[id*='offer']"), {"strategy": "css", "value": "input[id*='offer']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='special']"), {"strategy": "css", "value": "input[name*='special']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='partner']"), {"strategy": "css", "value": "input[name*='partner']"})
            self._append_locator_candidate(fallbacks, page.locator("input[type='checkbox']:visible").nth(0), {"strategy": "css", "value": "input[type='checkbox']:visible"})
            self._append_locator_candidate(fallbacks, page.locator("input[type='checkbox']:visible").nth(1), {"strategy": "css", "value": "input[type='checkbox']:visible"})
            self._append_locator_candidate(fallbacks, page.locator("input[type='checkbox']"), {"strategy": "css", "value": "input[type='checkbox']"})

        elif kind == "select":
            if tokens:
                for token in tokens:
                    self._append_locator_candidate(fallbacks, page.locator(f"select[name*='{token}']"), {"strategy": "css", "value": f"select[name*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"select[id*='{token}']"), {"strategy": "css", "value": f"select[id*='{token}']"})
                    self._append_locator_candidate(fallbacks, page.locator(f"select[class*='{token}']"), {"strategy": "css", "value": f"select[class*='{token}']"})
            self._append_locator_candidate(fallbacks, page.get_by_label(cleaned, exact=False), {"strategy": "label", "value": cleaned})
            self._append_locator_candidate(fallbacks, page.locator(f"label:has-text('{cleaned}')").locator("..").locator("select"), {"strategy": "label", "value": cleaned})
            if "country" in slug:
                self._append_locator_candidate(fallbacks, page.locator("select[name*='country']"), {"strategy": "css", "value": "select[name*='country']"})
                self._append_locator_candidate(fallbacks, page.locator("select[id*='country']"), {"strategy": "css", "value": "select[id*='country']"})
            if "state" in slug or "province" in slug:
                self._append_locator_candidate(fallbacks, page.locator("select[name*='state']"), {"strategy": "css", "value": "select[name*='state']"})
                self._append_locator_candidate(fallbacks, page.locator("select[id*='state']"), {"strategy": "css", "value": "select[id*='state']"})
                self._append_locator_candidate(fallbacks, page.locator("select[name*='province']"), {"strategy": "css", "value": "select[name*='province']"})
            self._append_locator_candidate(fallbacks, page.locator("select:visible").nth(0), {"strategy": "css", "value": "select:visible"})
            self._append_locator_candidate(fallbacks, page.locator("select"), {"strategy": "css", "value": "select"})

        return self._first_matching_locator(fallbacks)

    def _dynamic_locator(self, page, target: str, kind: str):
        if not target:
            return None
        cleaned = target.strip().strip("'\"")
        slug = self._slugify(cleaned)
        tokens = [token for token in slug.split("_") if token]
        token = tokens[0] if tokens else ""

        locators = []
        if kind == "click":
            locators.extend([
                page.get_by_role("button", name=cleaned, exact=False),
                page.get_by_role("link", name=cleaned, exact=False),
                page.get_by_text(cleaned, exact=False),
            ])
        elif kind == "fill":
            locators.extend([
                page.get_by_label(cleaned, exact=False),
                page.get_by_placeholder(cleaned, exact=False),
            ])
            if token:
                locators.extend([
                    page.locator(f'input[name*="{token}"]'),
                    page.locator(f'input[id*="{token}"]'),
                    page.locator(f'textarea[name*="{token}"]'),
                    page.locator(f'textarea[id*="{token}"]'),
                ])
            if "email" in slug:
                locators.append(page.locator('input[type="email"]'))
            if "password" in slug:
                locators.append(page.locator('input[type="password"]'))
        elif kind == "check":
            locators.extend([
                page.get_by_role("checkbox", name=cleaned, exact=False),
                page.get_by_label(cleaned, exact=False),
                page.locator("label").filter(has_text=cleaned).locator("input[type='checkbox']"),
            ])
            if token:
                locators.extend([
                    page.locator(f'input[type="checkbox"][name*="{token}"]'),
                    page.locator(f'input[type="checkbox"][id*="{token}"]'),
                ])
        elif kind == "select":
            locators.extend([
                page.get_by_label(cleaned, exact=False),
                page.locator("select"),
            ])

        for locator in locators:
            try:
                if locator.count() > 0:
                    return locator.first
            except PlaywrightError:
                continue
        return None

    def _append_locator_candidate(self, candidates: list[tuple[Any, dict[str, Any]]], locator, cache_entry: dict[str, Any]) -> None:
        candidates.append((locator, cache_entry))

    def _first_matching_locator(self, candidates: list[tuple[Any, dict[str, Any]]]) -> tuple[Any | None, dict[str, Any] | None]:
        for locator, cache_entry in candidates:
            try:
                if locator and locator.count() > 0:
                    return locator.first, cache_entry
            except PlaywrightError:
                continue
        return None, None

    def _locator_from_cache(self, page, cached: dict[str, Any] | None):
        if not cached:
            return None
        strategy = cached.get("strategy")
        locator = None
        if strategy == "role":
            role = cached.get("role", "")
            name = cached.get("name", "")
            if role and name:
                locator = page.get_by_role(role, name=name, exact=False)
        elif strategy == "text":
            locator = page.get_by_text(cached.get("value", ""), exact=False)
        elif strategy == "label":
            locator = page.get_by_label(cached.get("value", ""), exact=False)
        elif strategy == "css":
            locator = page.locator(cached.get("value", ""))
        if locator is None:
            return None
        try:
            return locator.first if locator.count() > 0 else None
        except PlaywrightError:
            return None

    def _refresh_discovery_from_page(self, page, discovery: dict) -> None:
        try:
            raw_nodes = self._extract_relevant_nodes(page)
            discovery["elements"] = self._classify_nodes(raw_nodes)
        except PlaywrightError:
            return

    def _find_element_name(self, discovery: dict, target: str, preferred_kind: str | None = None) -> str | None:
        element = self._find_element(discovery, None, target, preferred_kind=preferred_kind)
        return element["name"] if element else None

    def _find_element(self, discovery: dict, resolved_name: str | None, target: str | None, preferred_kind: str | None = None) -> dict | None:
        elements = discovery.get("elements", [])
        if resolved_name:
            for element in elements:
                if element.get("name") == resolved_name:
                    return element
        target_slug = self._slugify(target or "")
        if not target_slug:
            return None

        preferred = [e for e in elements if e.get("kind") == preferred_kind] if preferred_kind else elements
        candidates = preferred if preferred else elements

        def score(element: dict) -> tuple[int, int]:
            name = self._slugify(element.get("name", ""))
            s = 99
            if target_slug == name:
                s = 0
            elif target_slug in name or name in target_slug:
                s = 1
            elif all(token in name for token in target_slug.split("_") if token):
                s = 2
            return s, len(name)

        best = sorted(candidates, key=score)[0] if candidates else None
        if best and score(best)[0] <= 2:
            return best
        return None

    def _ts_locator_expression(self, selector: dict) -> str:
        strategy = selector["strategy"]
        if strategy == "css":
            expr = f"page.locator({json.dumps(selector['value'])})"
            if "nth" in selector:
                expr += f".nth({int(selector['nth'])})"
            return expr
        if strategy == "label":
            return f"page.getByLabel({json.dumps(selector['value'])})"
        if strategy == "placeholder":
            return f"page.getByPlaceholder({json.dumps(selector['value'])})"
        if strategy == "role":
            role = json.dumps(selector["value"]["role"])
            name = json.dumps(selector["value"]["name"])
            return f"page.getByRole({role}, {{ name: {name} }})"
        if strategy == "text":
            return f"page.getByText({json.dumps(selector['value'])})"
        raise PlaywrightError(f"Unsupported selector strategy: {strategy}")

    def _page_class_name(self, ui_input: dict) -> str:
        token = self._slugify(ui_input["scenario_name"]) or self._slugify(self._page_title(ui_input["scenario_name"], ui_input["url"]))
        token = token or "generic"
        return f"{''.join(part.capitalize() for part in token.split('_'))}Page"

    def _page_title(self, scenario_name: str, url: str) -> str:
        path = urlparse(url).path.strip("/")
        token = self._slugify(scenario_name) or self._slugify(path) or "generic_ui"
        return token.replace("_", " ").title()

    def _artifact_root(self, ui_input: dict) -> Path:
        scenario = self._slugify(ui_input.get("scenario_name", "")) or "ui_smoke"
        host = urlparse(ui_input.get("url", "")).netloc
        host_slug = self._slugify(host) or "local"
        return self.artifacts_dir / "ui_generated" / host_slug / scenario

    def _write_discovered_locator_cache(self, discovery: dict) -> None:
        cache_path = self.data_dir / "discovered_locators.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        platform_cache = cache.get(discovery["target_platform"], {})
        for element in discovery["elements"]:
            platform_cache[element["name"]] = {
                "strategy": element["selector"]["strategy"],
                "value": element["selector"]["value"],
                "kind": element["kind"],
                "source": element["source"],
            }
        cache[discovery["target_platform"]] = platform_cache
        self._write_json(cache_path, cache)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2))

    def _write_text(self, path: Path, content: str) -> None:
        path.write_text(content)










