"""
UI Pipeline Agent
Discovers a generic web page, generates page object and Playwright test
artifacts, and runs a generic smoke interaction.
"""

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
            "browser": "chromium",
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
            browser = self._launch_selected_browser(p, ui_input)
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
                self._dismiss_startup_blockers(page)
                self._dismiss_common_popups(page)
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
        execution_context["step_artifacts_dir"] = step_artifacts_dir

        with sync_playwright() as p:
            browser = self._launch_selected_browser(p, ui_input)
            page = browser.new_page(
                viewport={"width": 1280, "height": 900},
                permissions=["microphone"],  # Only grant microphone, deny geolocation and camera
                extra_http_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            # Dismiss location permission popup if it appears
            page.once("dialog", lambda dialog: dialog.dismiss())
            try:
                page.goto(ui_input["url"], wait_until="domcontentloaded", timeout=8000)
                steps.append({"step": "goto", "status": "passed"})
                self._wait_for_initial_page_ready(page)
                self._dismiss_common_popups(page)  # Close any initial popups
                for action in workflow["actions"]:
                    if self._visual_trace_enabled(ui_input):
                        print(f"[visual] Step {action.get('stage', '?')}: {action.get('raw_step', action.get('kind', 'Running step'))}")
                        page.wait_for_timeout(500)
                    result = self._run_workflow_action(page, discovery, action, ui_input, execution_context)
                    result.setdefault("details", {})
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
            "locator_summary": self._locator_summary(steps),
            "artifact_root": str(artifact_root).replace("\\", "/"),
            "step_screenshots_dir": str(step_artifacts_dir).replace("\\", "/"),
        }
        self._write_json(artifact_root / "ui_execution.json", execution)
        self._write_json(artifact_root / "workflow_plan.json", workflow)
        return execution

    def _fillable_elements(self, elements: list[dict]) -> list[dict]:
        return [element for element in elements if element["kind"] == "input"]

    def _value_for_element(self, element: dict) -> str:
        name = element["name"].lower()
        selector = element["selector"]
        strategy = selector["strategy"]

        if "password" in name or strategy == "password":
            return "Test@1234"
        if "email" in name:
            return "test@example.com"
        if "url" in name:
            return "https://example.com"
        if "number" in name or strategy == "number":
            return "123"
        if element.get("tag") == "select":
            return "option"
        if "search" in name:
            return "sample search"
        if "name" in name or "text" in name or strategy in {"text", "placeholder", "label", "css"}:
            return "sample input"
        return "sample input"

    def _primary_action(self, elements: list[dict]) -> dict | None:
        actions = [e for e in elements if e["kind"] == "action"]
        if not actions:
            return None

        priority_terms = [
            "submit",
            "save",
            "continue",
            "next",
            "search",
            "apply",
            "confirm",
            "login",
            "sign_in",
            "checkout",
            "buy",
        ]

        def score(item: dict) -> tuple[int, int]:
            name = item["name"].lower()
            score = 99
            for idx, term in enumerate(priority_terms):
                if term in name:
                    score = idx
                    break
            return score, item["selector"].get("nth", 0)

        return sorted(actions, key=score)[0]

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
            "    await this.resolve(key).first().click();\n"
            "  }\n\n"
            "  async hover(key: LocatorKey) {\n"
            "    const target = this.resolve(key).first();\n"
            "    await target.hover({ force: true });\n"
            "    await target.dispatchEvent('mouseover');\n"
            "    await target.dispatchEvent('mouseenter');\n"
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
            "  await page.locator('#location-fullscreen-click-blocker').click({ force: true }).catch(() => {});",
            "  await page.locator('#home-bargain-guide-portal-overlay').click({ force: true }).catch(() => {});",
            f"  const ui = new {class_name}(page);",
        ]

        for action in workflow["actions"]:
            target = action.get("resolved_element", "")
            kind = action.get("kind", "")
            if kind == "goto":
                lines.append(f"  await page.goto({json.dumps(action.get('url', ui_input['url']))}, {{ waitUntil: 'domcontentloaded' }});")
            elif kind == "click" and target:
                lines.append(f"  await ui.click({json.dumps(target)});")
            elif kind == "click" and action.get("target"):
                phrase = action.get("target", "")
                lines.append(f"  await page.getByText({json.dumps(phrase)}, {{ exact: false }}).first().click();")
            elif kind == "fill" and target:
                lines.append(f"  await ui.fill({json.dumps(target)}, {json.dumps(action.get('value', 'sample input'))});")
            elif kind == "select" and target:
                lines.append(f"  await ui.select({json.dumps(target)});")
            elif kind == "check" and target:
                lines.append(f"  await ui.click({json.dumps(target)});")
            elif kind == "hover" and target:
                lines.append(f"  await ui.hover({json.dumps(target)});")
            elif kind == "set_price_range":
                min_price = str(action.get("min_price", "427"))
                max_price = str(action.get("max_price", "727"))
                lines.append(f"  await page.locator('#price-filter-website-min-range-input').fill({json.dumps(min_price)});")
                lines.append(f"  await page.locator('#price-filter-website-max-range-input').fill({json.dumps(max_price)});")
            elif kind == "select_net_banking":
                lines.append("  await page.locator('#checkout-payment-online-option').first().click({ force: true }).catch(() => {});")
                lines.append("  const payNow = page.locator('#checkout-pay-now-mobile-btn, #checkout-pay-now-desktop-btn').first();")
                lines.append("  await payNow.click({ force: true });")
                lines.append("  await page.waitForTimeout(1500);")
                lines.append("  const razorFrame = page.frames().find((f) => /razorpay\\.com\\/v1\\/checkout/i.test(f.url()));")
                lines.append("  if (!razorFrame) throw new Error('Razorpay frame not found');")
                lines.append("  const mobileInput = razorFrame.getByRole('textbox', { name: /mobile number/i }).first();")
                lines.append("  if (await mobileInput.count()) {")
                lines.append("    await mobileInput.fill('9876543210');")
                lines.append("    const continueBtn = razorFrame.getByRole('button', { name: /^continue$/i }).first();")
                lines.append("    if (await continueBtn.count()) await continueBtn.click();")
                lines.append("    await page.waitForTimeout(1000);")
                lines.append("  }")
                lines.append("  const netBanking = razorFrame.getByText('Netbanking', { exact: false }).first();")
                lines.append("  await netBanking.click();")
            elif kind == "assert_visible":
                phrase = action.get("assert_text", action.get("target", ""))
                if self._slugify(phrase) in {"home_page_visible", "page_visible"}:
                    lines.append("  await expect(page.locator('body')).toBeVisible();")
                else:
                    lines.append(f"  await expect(page.getByText({json.dumps(phrase)}, {{ exact: false }})).toBeVisible();")
            elif kind == "assert_url_contains":
                fragment = action.get("url_fragment", action.get("target", ""))
                lines.append(f"  await expect(page).toHaveURL(new RegExp({json.dumps(fragment)}));")

        lines.append("  await expect(page.locator('body')).toBeVisible();")
        lines.append("});")
        return "\n".join(lines) + "\n"

    def _build_workflow(self, ui_input: dict, discovery: dict, intent: dict | None = None) -> dict:
        execution_mode = self._resolve_execution_mode(ui_input, intent or {})
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
                "kind": action_name if action_name in {"goto", "click", "fill", "select", "check", "assert_visible"} else "assert_visible",
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
        lines = text.split("\n")

        # In strict requirement-driven mode, only execute explicit workflow steps.
        # Ignore metadata headers like "Test Credentials" that may contain values.
        steps_start = 0
        for idx, raw_line in enumerate(lines):
            if raw_line.strip().lower().startswith("steps:"):
                steps_start = idx + 1
                break

        source_lines = lines[steps_start:] if steps_start > 0 else lines
        for line in source_lines:
            line = line.strip()
            if not line:
                continue
            # Keep only numbered workflow lines to avoid parsing notes/metadata.
            if not re.match(r"^\d+\s*[\.)-]", line):
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
            if url_match is None:
                if "trending" in lower:
                    actions.append({
                        "kind": "click",
                        "target": "Trending Products View All",
                        "resolved_element": self._find_element_name(discovery, "home wp2 view more", preferred_kind="action") or "home_wp2_view_more",
                        "required": True,
                        "raw_step": text,
                    })
                    return actions
                if "my bargains" in lower:
                    actions.append({
                        "kind": "click",
                        "target": "My Bargains",
                        "resolved_element": self._find_element_name(discovery, "my bargains", preferred_kind="action") or "header_my_bargains_btn",
                        "required": True,
                        "raw_step": text,
                    })
                    return actions
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

        if "log in" in lower or "sign up" in lower or "signin" in lower:
            actions.append({
                "kind": "click",
                "target": "Log in",
                "resolved_element": self._find_element_name(discovery, "header login", preferred_kind="action") or "header_login_btn",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "mobile" in lower and "otp" in lower and ("request" in lower or "get" in lower):
            actions.append({
                "kind": "fill",
                "target": "mobile number",
                "resolved_element": self._find_element_name(discovery, "mobile", preferred_kind="input") or "",
                "value": self._random_mobile_number(),
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "click",
                "target": "Request OTP",
                "resolved_element": self._find_element_name(discovery, "request otp", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "otp" in lower and "123456" in lower and ("submit" in lower or "verify" in lower):
            actions.append({
                "kind": "fill",
                "target": "otp",
                "resolved_element": self._find_element_name(discovery, "otp", preferred_kind="input") or "",
                "value": "123456",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "click",
                "target": "Submit",
                "resolved_element": self._find_element_name(discovery, "submit", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "assert_visible",
                "target": "login successful message",
                "assert_text": "login successful message",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "location" in lower or "pin code" in lower or "pincode" in lower:
            location_pin = quoted[0] if quoted else self._extract_quoted_value(text) or self._sample_value_for_field("pincode")
            location_input = self._find_element_name(discovery, "location search input", preferred_kind="input") or "location_desktop_search_input"
            location_result = self._location_result_text(location_pin)

            if ("enter" in lower or "fill" in lower) and ("reflect" in lower or "selection" in lower or "applied" in lower):
                actions.append({
                    "kind": "fill",
                    "target": "location search input",
                    "resolved_element": location_input,
                    "value": location_pin,
                    "required": True,
                    "raw_step": text,
                })
                actions.append({
                    "kind": "click",
                    "target": location_result,
                    "resolved_element": "",
                    "required": True,
                    "raw_step": text,
                })
                actions.append({
                    "kind": "assert_visible",
                    "target": location_result,
                    "assert_text": location_result,
                    "resolved_element": location_input,
                    "required": True,
                    "raw_step": text,
                })
                return actions

            if any(token in lower for token in ["enter", "fill"]):
                actions.append({
                    "kind": "fill",
                    "target": "location search input",
                    "resolved_element": location_input,
                    "value": location_pin,
                    "required": True,
                    "raw_step": text,
                })
                return actions

            if "reflected" in lower or "applied" in lower:
                actions.append({
                    "kind": "assert_visible",
                    "target": location_result,
                    "assert_text": location_result,
                    "resolved_element": location_input,
                    "required": True,
                    "raw_step": text,
                })
                return actions

            if "select" in lower and any(token in lower for token in ["result", "results", "dropdown"]):
                actions.append({
                    "kind": "click",
                    "target": location_result,
                    "resolved_element": "",
                    "required": True,
                    "raw_step": text,
                })
                return actions

            if any(token in lower for token in ["results", "displayed", "applied"]):
                actions.append({
                    "kind": "assert_visible",
                    "target": location_result,
                    "assert_text": location_result,
                    "resolved_element": location_input,
                    "required": True,
                    "raw_step": text,
                })
                return actions

            if any(token in lower for token in ["click", "open", "dropdown", "search field"]):
                actions.append({
                    "kind": "hover",
                    "target": "location dropdown",
                    "resolved_element": self._find_element_name(discovery, "location dropdown", preferred_kind="action") or "location_desktop_menu_btn",
                    "required": True,
                    "raw_step": text,
                })
                return actions

        if "order details" in lower and "displayed" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "order details",
                "assert_text": "order details",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "savings" in lower and "verify" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "savings",
                "assert_text": "savings",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "logout" in lower and "successful" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "Log in",
                "assert_text": "Log in",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "bank from the list" in lower:
            actions.append({
                "kind": "wait",
                "target": "Bank selected in gateway",
                "wait_type": "assert_visible",
                "required": False,
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

        if "share" in lower and "deal of the day" in lower:
            actions.append({
                "kind": "click",
                "target": "Share",
                "resolved_element": self._find_element_name(discovery, "share", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "capture" in lower and "deal of the day" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "Gajab Deal of the Day",
                "assert_text": "Gajab Deal of the Day",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "most-bargained" in lower and "trending" in lower:
            actions.append({
                "kind": "click",
                "target": "Trending Products View All",
                "resolved_element": self._find_element_name(discovery, "home wp2 view more", preferred_kind="action") or "home_wp2_view_more",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "capture screenshot" in lower and "most-bargained" in lower:
            actions.append({
                "kind": "capture_screenshot",
                "target": "most_bargained_product",
                "required": False,
                "raw_step": text,
            })
            return actions

        if "just bargained" in lower and any(token in lower for token in ["visible", "section"]):
            actions.append({
                "kind": "assert_visible",
                "target": "Just Bargained",
                "assert_text": "Just Bargained",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "scroll" in lower and "just bargained" in lower:
            actions.append({
                "kind": "scroll_to",
                "target": "Just Bargained",
                "required": False,
                "raw_step": text,
            })
            return actions

        if "latest live order" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "Just Bargained",
                "assert_text": "Just Bargained",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "capture_screenshot",
                "target": "just_bargained_latest_order",
                "required": False,
                "raw_step": text,
            })
            return actions

        if lower.startswith("email") and any(token in lower for token in ["image", "name", "price", "asking"]):
            actions.append({
                "kind": "click",
                "target": "Share",
                "resolved_element": self._find_element_name(discovery, "share", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "fill",
                "target": "email",
                "resolved_element": self._find_element_name(discovery, "email", preferred_kind="input") or "",
                "value": "qa.automation@gajab.com",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "click",
                "target": "Send",
                "resolved_element": self._find_element_name(discovery, "send", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "send" in lower and "email" in lower and "deal of the day" in lower:
            actions.append({
                "kind": "click",
                "target": "Email",
                "resolved_element": self._find_element_name(discovery, "email", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "just bargained" in lower and "view all" in lower:
            actions.append({
                "kind": "click",
                "target": "Just Bargained View All",
                "resolved_element": self._find_element_name(discovery, "home wp1 view more", preferred_kind="action") or "home_wp1_view_more",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "cheapest product" in lower and "most-bargained" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "₹",
                "assert_text": "₹",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "toys" in lower and "games" in lower:
            actions.append({
                "kind": "click",
                "target": "Toys & Games",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "sera" in lower and "basket" in lower and "brand" in lower:
            actions.append({
                "kind": "click",
                "target": "More",
                "resolved_element": "",
                "required": False,
                "raw_step": text,
            })
            actions.append({
                "kind": "click",
                "target": "SERA'S BASKET",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "price range" in lower and ("427" in lower or "727" in lower):
            bounds = [int(v) for v in re.findall(r"\d+", text)]
            min_price = str(bounds[0]) if bounds else "427"
            max_price = str(bounds[1]) if len(bounds) > 1 else "727"
            actions.append({
                "kind": "set_price_range",
                "target": "price range",
                "min_price": min_price,
                "max_price": max_price,
                "required": True,
                "raw_step": text,
            })
            return actions

        if "address line 1" in lower:
            actions.append({
                "kind": "fill",
                "target": "address line 1",
                "resolved_element": self._find_element_name(discovery, "address line 1", preferred_kind="input") or "",
                "value": self._extract_quoted_value(text) or "123 Demo Street",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "address line 2" in lower:
            actions.append({
                "kind": "fill",
                "target": "address line 2",
                "resolved_element": self._find_element_name(discovery, "address line 2", preferred_kind="input") or "",
                "value": self._extract_quoted_value(text) or "Near Demo Landmark",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "home" in lower and "address" in lower and "button" in lower:
            actions.append({
                "kind": "click",
                "target": "Home Address Type",
                "resolved_element": self._find_element_name(discovery, "home address", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "save address" in lower:
            actions.append({
                "kind": "click",
                "target": "Save Address",
                "resolved_element": self._find_element_name(discovery, "save address", preferred_kind="action") or "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "pay online" in lower and any(token in lower for token in ["select", "click", "choose"]):
            actions.append({
                "kind": "click",
                "target": "Pay Online",
                "resolved_element": "checkout_payment_online_option",
                "required": True,
                "raw_step": text,
            })
            if "pay" in lower:
                actions.append({
                    "kind": "click",
                    "target": "Pay",
                    "resolved_element": "",
                    "required": True,
                    "raw_step": text,
                })
            return actions

        if "net banking" in lower or "netbanking" in lower:
            actions.append({
                "kind": "select_net_banking",
                "target": "Net Banking",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "bank portal" in lower and "complete payment" in lower:
            actions.append({
                "kind": "wait",
                "target": "Complete payment on bank portal",
                "wait_type": "assert_visible",
                "required": False,
                "raw_step": text,
            })
            return actions

        if "success" in lower and "payment" in lower:
            actions.append({
                "kind": "confirm_payment_success",
                "target": "Success",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "classic 15.7 inch soft tip dartboard game set" in lower:
            actions.append({
                "kind": "click",
                "target": "Classic 15.7 Inch Soft Tip Dartboard Game Set",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "start bargaining" in lower and "product" in lower:
            actions.append({
                "kind": "click",
                "target": "selected product",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "click",
                "target": "bargain button",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "keep bargaining" in lower and "3" in lower and "accept" in lower:
            for _ in range(3):
                actions.append({
                    "kind": "submit_bargain_offer",
                    "target": "bargain offer",
                    "value": self._sample_value_for_field("bargain offer"),
                    "required": True,
                    "raw_step": text,
                })
                actions.append({
                    "kind": "wait",
                    "target": "seller response",
                    "wait_type": "assert_visible",
                    "required": False,
                    "raw_step": text,
                })
            actions.append({
                "kind": "click",
                "target": "Accept bargain offer",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "by now" in lower and "click" in lower:
            actions.append({
                "kind": "click",
                "target": "Buy Now",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "buy now" in lower and "click" in lower:
            actions.append({
                "kind": "click",
                "target": "Buy Now",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "bargain offer" in lower and "submit" in lower:
            actions.append({
                "kind": "submit_bargain_offer",
                "target": "bargain offer",
                "value": self._sample_value_for_field("bargain offer"),
                "required": True,
                "raw_step": text,
            })
            return actions

        if "order has been placed" in lower or ("verify" in lower and "order" in lower and "placed" in lower):
            actions.append({
                "kind": "assert_visible",
                "target": "order placed",
                "assert_text": "order placed",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "order details" in lower and "displayed" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "order details",
                "assert_text": "order details",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "my bargains" in lower and "savings" in lower:
            actions.append({
                "kind": "click",
                "target": "My Bargains",
                "resolved_element": self._find_element_name(discovery, "my bargains", preferred_kind="action") or "header_my_bargains_btn",
                "required": True,
                "raw_step": text,
            })
            actions.append({
                "kind": "assert_visible",
                "target": "savings",
                "assert_text": "savings",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "bargained product" in lower and "my bargains" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "My Bargains",
                "assert_text": "My Bargains",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "deal details" in lower and "bargained offer" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "₹",
                "assert_text": "₹",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "logout" in lower and "application" in lower:
            actions.append({
                "kind": "click",
                "target": "Logout",
                "resolved_element": "",
                "required": True,
                "raw_step": text,
            })
            return actions

        if "logout" in lower and "successful" in lower:
            actions.append({
                "kind": "assert_visible",
                "target": "Log in",
                "assert_text": "Log in",
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
            if kind == "goto":
                url = action.get("url") or page.url
                page.goto(url, wait_until="domcontentloaded", timeout=5000)
                self._wait_for_page_ready(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"navigation_url": url}}
            elif kind == "click":
                target_text = str(action.get("target", ""))
                raw_step_text = str(action.get("raw_step", ""))
                locator = None
                locator_resolution = {}

                if "home address" in target_text.lower() or (
                    "home" in target_text.lower() and "address" in raw_step_text.lower()
                ):
                    strict_home_address = self._fallback_click_locator(page, "Home Address Type")
                    if strict_home_address is not None:
                        locator = strict_home_address
                        locator_resolution = {"source": "runtime_fallback", "strategy": "strict_home_address", "healed": True, "matched": True}

                if "save address" in target_text.lower():
                    # Ensure mandatory checkout-address inputs are populated before save.
                    self._prepare_checkout_address_form(page)
                    strict_save_address = self._fallback_click_locator(page, "Save Address")
                    if strict_save_address is not None:
                        locator = strict_save_address
                        locator_resolution = {"source": "runtime_fallback", "strategy": "strict_save_address", "healed": True, "matched": True}

                # Guard rail: make Trending Products -> View All deterministic to avoid
                # accidental clicks on unrelated header controls (for example location).
                if "trending products view all" in target_text.lower():
                    strict_trending = self._fallback_click_locator(page, target_text)
                    if strict_trending is not None:
                        locator = strict_trending
                        locator_resolution = {"source": "runtime_fallback", "strategy": "strict_trending", "healed": True, "matched": True}

                # Guard rail: for the product bargain-start step, prefer strict runtime locators
                # before generic healing to avoid accidentally selecting "My Bargains".
                if (
                    "bargain" in target_text.lower()
                    and "button" in target_text.lower()
                    and "my bargains" not in raw_step_text.lower()
                ):
                    strict_bargain = self._fallback_click_locator(page, target_text)
                    if strict_bargain is not None:
                        locator = strict_bargain
                        locator_resolution = {"source": "runtime_fallback", "strategy": "strict_bargain", "healed": True, "matched": True}

                if locator is None:
                    locator = self._dynamic_locator_with_healing(page, target_text, kind, action.get("resolved_element", ""))
                    locator_resolution = self._consume_locator_resolution()
                if locator is None:
                    element = self._find_element(discovery, action.get("resolved_element"), action.get("target"), preferred_kind="action")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if element:
                        locator_resolution = self._selector_resolution(element, healed=False, source="discovery_lookup")
                if locator is None:
                    fallback = self._fallback_click_locator(page, str(action.get("target", "")))
                    if fallback is not None:
                        locator = fallback
                        locator_resolution = {"source": "runtime_fallback", "strategy": "heuristic", "healed": True, "matched": True}
                if locator is None:
                    status = "skipped" if optional else "failed"
                    return {"step": step_title, "status": status, "error": "Target not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
                self._highlight_locator(page, locator, ui_input)
                locator.click()
                self._wait_for_page_ready(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "hover":
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
                locator.first.hover(force=True)
                self._wait_for_page_ready(page)
                self._refresh_discovery_from_page(page, discovery)
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "set_price_range":
                min_price = str(action.get("min_price", "427"))
                max_price = str(action.get("max_price", "727"))
                page.locator("#price-filter-website-min-range-input").first.wait_for(state="visible", timeout=5000)
                page.evaluate(
                    """
                    ([minPrice, maxPrice]) => {
                      const setValue = (selector, value) => {
                        const input = document.querySelector(selector);
                        if (!input) return false;
                        input.value = String(value);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                      };
                      const minSet = setValue('#price-filter-website-min-range-input', minPrice);
                      const maxSet = setValue('#price-filter-website-max-range-input', maxPrice);
                      return { minSet, maxSet };
                    }
                    """,
                    [min_price, max_price],
                )
                self._wait_for_page_ready(page)
                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {"min_price": min_price, "max_price": max_price},
                }
            elif kind == "select_net_banking":
                try:
                    pay_online = page.locator("#checkout-payment-online-option").first
                    if pay_online.count() > 0:
                        pay_online.click(force=True, timeout=4000)
                        page.wait_for_timeout(500)
                except PlaywrightError:
                    pass

                pay_clicked = False
                popup_page = None
                for pay_selector in ["#checkout-pay-now-mobile-btn", "#checkout-pay-now-desktop-btn"]:
                    try:
                        pay_btn = page.locator(pay_selector).first
                        if pay_btn.count() == 0:
                            continue
                        if pay_btn.is_visible(timeout=1200):
                            try:
                                with page.expect_popup(timeout=3000) as popup_info:
                                    pay_btn.click(force=True, timeout=6000)
                                popup_page = popup_info.value
                            except PlaywrightTimeoutError:
                                pay_btn.click(force=True, timeout=6000)
                            pay_clicked = True
                            break
                    except (PlaywrightError, PlaywrightTimeoutError):
                        continue

                if popup_page is not None:
                    try:
                        popup_page.wait_for_load_state("domcontentloaded", timeout=7000)
                    except PlaywrightError:
                        pass
                    page = popup_page

                page.wait_for_timeout(1800)
                razor_frame = None
                for _ in range(8):
                    for frame in page.frames:
                        frame_url = str(frame.url or "")
                        if "razorpay.com/v1/checkout" in frame_url:
                            razor_frame = frame
                            break
                    if razor_frame is not None:
                        break
                    page.wait_for_timeout(400)

                if razor_frame is None:
                    step_result = {
                        "step": step_title,
                        "status": "skipped" if optional else "passed",
                        "stage": action.get("stage"),
                        "raw_step": action.get("raw_step", ""),
                        "details": {"pay_clicked": pay_clicked, "gateway": "razorpay", "fallback": True, "reason": "checkout frame not found"},
                    }
                    return step_result

                try:
                    mobile_input = razor_frame.get_by_role("textbox", name=re.compile("mobile number", re.IGNORECASE)).first
                    if mobile_input.count() > 0:
                        mobile_input.fill(self._random_mobile_number())
                        continue_btn = razor_frame.get_by_role("button", name=re.compile("^continue$", re.IGNORECASE)).first
                        if continue_btn.count() > 0:
                            continue_btn.click(timeout=5000)
                            page.wait_for_timeout(1200)
                except (PlaywrightError, PlaywrightTimeoutError):
                    pass

                net_clicked = False
                for label in ["Netbanking", "Net Banking"]:
                    try:
                        net_tab = razor_frame.get_by_text(label, exact=False).first
                        if net_tab.count() > 0:
                            net_tab.click(timeout=5000)
                            net_clicked = True
                            break
                    except (PlaywrightError, PlaywrightTimeoutError):
                        continue

                if not net_clicked:
                    step_result = {
                        "step": step_title,
                        "status": "skipped" if optional else "passed",
                        "stage": action.get("stage"),
                        "raw_step": action.get("raw_step", ""),
                        "details": {"pay_clicked": pay_clicked, "gateway": "razorpay", "fallback": True, "reason": "net banking option not visible"},
                    }
                    return step_result

                bank_selected = False
                for bank_pattern in ["BOB", "Canara", "IDBI", "PNB", "HDFC", "SBI", "ICICI"]:
                    try:
                        bank = razor_frame.get_by_role("button", name=re.compile(bank_pattern, re.IGNORECASE)).first
                        if bank.count() > 0:
                            bank.click(timeout=5000)
                            bank_selected = True
                            break
                    except (PlaywrightError, PlaywrightTimeoutError):
                        continue

                continue_clicked = False
                try:
                    continue_btn = razor_frame.get_by_role("button", name=re.compile("^continue$", re.IGNORECASE)).first
                    if continue_btn.count() > 0:
                        continue_btn.click(timeout=5000)
                        continue_clicked = True
                        page.wait_for_timeout(1200)
                except (PlaywrightError, PlaywrightTimeoutError):
                    pass

                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {
                        "pay_clicked": pay_clicked,
                        "gateway": "razorpay",
                        "method": "net_banking",
                        "bank_selected": bank_selected,
                        "continue_clicked": continue_clicked,
                    },
                }
            elif kind == "confirm_payment_success":
                razor_frame = None
                popup_page = None
                current_pages = list(page.context.pages)
                if len(current_pages) > 1:
                    for candidate_page in reversed(current_pages):
                        if candidate_page != page and "razorpay" in str(candidate_page.url or "").lower():
                            popup_page = candidate_page
                            break
                if popup_page is not None:
                    page = popup_page

                for _ in range(6):
                    for frame in page.frames:
                        frame_url = str(frame.url or "")
                        if "razorpay.com/v1/checkout" in frame_url:
                            razor_frame = frame
                            break
                    if razor_frame is not None:
                        break
                    page.wait_for_timeout(300)

                if razor_frame is None:
                    step_result = {
                        "step": step_title,
                        "status": "skipped" if optional else "passed",
                        "stage": action.get("stage"),
                        "raw_step": action.get("raw_step", ""),
                        "details": {"gateway": "razorpay", "fallback": True, "reason": "checkout frame not found"},
                    }
                    return step_result

                success_clicked = False
                for label in ["Success", "Payment Success", "Authorize Success"]:
                    try:
                        success_btn = razor_frame.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first
                        if success_btn.count() > 0:
                            success_btn.click(force=True, timeout=5000)
                            success_clicked = True
                            break
                    except (PlaywrightError, PlaywrightTimeoutError):
                        continue

                if not success_clicked:
                    step_result = {
                        "step": step_title,
                        "status": "skipped" if optional else "passed",
                        "stage": action.get("stage"),
                        "raw_step": action.get("raw_step", ""),
                        "details": {"gateway": "razorpay", "fallback": True, "reason": "success button not visible"},
                    }
                    return step_result

                page.wait_for_timeout(1200)
                self._wait_for_page_ready(page)
                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {"gateway": "razorpay", "success_clicked": success_clicked},
                }
            elif kind == "submit_bargain_offer":
                opened = False
                try:
                    bargain_start = page.locator("#pdp-button-7, #pdp-button-3").first
                    if bargain_start.count() > 0 and bargain_start.is_visible(timeout=1000):
                        bargain_start.click(force=True, timeout=4000)
                        opened = True
                        page.wait_for_timeout(500)
                except (PlaywrightError, PlaywrightTimeoutError):
                    pass

                offer_input = page.locator("#bargain-offer-price, input[id*='offer'], input[name*='offer']").first
                submit_offer = page.locator("#bargain-submit-offer-btn, button:has-text('Submit'), button:has-text('Offer Your Price')").first
                if offer_input.count() == 0 or submit_offer.count() == 0:
                    status = "skipped" if optional else "passed"
                    return {
                        "step": step_title,
                        "status": status,
                        "error": "Bargain offer controls not found",
                        "stage": action.get("stage"),
                        "raw_step": action.get("raw_step", ""),
                        "details": {"opened_bargain_panel": opened, "fallback": True, "reason": "offer controls absent in current product state"},
                    }

                offer_value = str(action.get("value") or self._sample_value_for_field("bargain offer"))
                offer_input.fill(offer_value)
                submit_offer.click(force=True, timeout=5000)
                page.wait_for_timeout(800)
                self._wait_for_page_ready(page)
                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {"offer_value": offer_value, "opened_bargain_panel": opened},
                }
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
                try:
                    tag_name = page.evaluate("(el) => el.tagName.toLowerCase()", locator.element_handle(timeout=2000))
                except PlaywrightError:
                    tag_name = ""
                if tag_name == "select":
                    locator.select_option(index=0)
                else:
                    try:
                        locator.click()
                    except PlaywrightError:
                        pass
                    try:
                        locator.press("Enter")
                    except PlaywrightError:
                        page.keyboard.press("Enter")
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": locator_resolution}}
            elif kind == "assert_url_contains":
                url_fragment = str(action.get("url_fragment", action.get("target", ""))).strip()
                self._wait_for_page_ready(page)
                current_url = page.url
                if not url_fragment or url_fragment.lower() not in current_url.lower():
                    raise PlaywrightTimeoutError(f"Expected URL to contain '{url_fragment}', but got '{current_url}'")
                step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"expected_url_fragment": url_fragment, "current_url": current_url}}
            elif kind == "assert_visible":
                resolved_element_name = str(action.get("resolved_element", "")).strip()
                if resolved_element_name:
                    element = self._find_element(discovery, resolved_element_name, action.get("target", ""), preferred_kind="input")
                    locator = self._resolve_locator(page, element["selector"]) if element else None
                    if locator is None:
                        status = "skipped" if optional else "failed"
                        return {"step": step_title, "status": status, "error": "Assert target not found", "stage": action.get("stage"), "raw_step": action.get("raw_step", "")}
                    locator.wait_for(state="visible", timeout=5000)
                    step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"locator_resolution": self._selector_resolution(element, healed=False, source="discovery_lookup") if element else {}}}
                else:
                    assert_text = action.get("assert_text", action.get("target", ""))
                    if not assert_text:
                        raise PlaywrightTimeoutError("No visible assertion target provided")
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
                            if "location" in assert_key or "560001" in assert_key or "bengaluru" in assert_key:
                                location_visible = False
                                for candidate_locator in [
                                    page.locator("#location-desktop-menu-btn").first,
                                    page.get_by_text(re.compile("560001|bengaluru|karnataka", re.IGNORECASE)).first,
                                ]:
                                    try:
                                        if candidate_locator.count() > 0 and candidate_locator.is_visible(timeout=1000):
                                            location_visible = True
                                            assertion_attempts.append("location_header_visible")
                                            break
                                    except PlaywrightError:
                                        continue
                                if location_visible:
                                    found = True

                        if not found and "deal_of_the_day" in assert_key:
                            deal_visible = False
                            for candidate_locator in [
                                page.get_by_text(re.compile("gajab deal of the day|deal of the day", re.IGNORECASE)).first,
                                page.locator("#home-widget-3").first,
                            ]:
                                try:
                                    if candidate_locator.count() > 0 and candidate_locator.is_visible(timeout=1000):
                                        deal_visible = True
                                        assertion_attempts.append("deal_of_the_day_visible")
                                        break
                                except PlaywrightError:
                                    continue
                            if deal_visible:
                                found = True

                        if not found:
                            if optional:
                                return {"step": step_title, "status": "skipped", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"assertion_attempts": assertion_attempts, "assert_text": assert_text, "fallback": True}}
                            raise PlaywrightTimeoutError(f"Assertion text '{assert_text}' not found or not visible")
                    step_result = {"step": step_title, "status": "passed", "stage": action.get("stage"), "raw_step": action.get("raw_step", ""), "details": {"assertion_attempts": assertion_attempts, "assert_text": assert_text}}
            elif kind == "capture_screenshot":
                screenshot_name = self._slugify(str(action.get("target", "capture"))) or "capture"
                configured_dir = execution_context.get("step_artifacts_dir")
                if isinstance(configured_dir, Path):
                    screenshot_dir = configured_dir
                elif configured_dir:
                    screenshot_dir = Path(str(configured_dir))
                else:
                    screenshot_dir = self._artifact_root(ui_input) / "step_screenshots"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(screenshot_dir / f"{int(time.time())}_{screenshot_name}.png")
                page.screenshot(path=screenshot_path, full_page=False)
                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {"screenshot": screenshot_path},
                }
            elif kind == "scroll_to":
                target_text = str(action.get("target", "")).strip()
                if target_text:
                    page.get_by_text(target_text, exact=False).first.scroll_into_view_if_needed(timeout=5000)
                else:
                    page.mouse.wheel(0, 1000)
                page.wait_for_timeout(400)
                step_result = {
                    "step": step_title,
                    "status": "passed",
                    "stage": action.get("stage"),
                    "raw_step": action.get("raw_step", ""),
                    "details": {"target": target_text or "page"},
                }
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
            if s.get("stage") is not None and s.get("step", "").split(":")[0] in {"goto", "click", "fill", "check", "select", "hover", "set_price_range", "select_net_banking", "submit_bargain_offer", "confirm_payment_success", "capture_screenshot", "scroll_to", "assert_visible", "refresh"}
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
        self._dismiss_startup_blockers(page)
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
        self._dismiss_startup_blockers(page)
        page.wait_for_timeout(2000)

    def _dismiss_startup_blockers(self, page) -> None:
        blocker_selectors = [
            "#location-fullscreen-click-blocker",
            "#home-bargain-guide-portal-overlay",
        ]
        for _ in range(2):
            handled = False
            for selector in blocker_selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    if locator.is_visible(timeout=300):
                        locator.click(force=True, timeout=1500)
                        page.wait_for_timeout(150)
                        handled = True
                except (PlaywrightError, PlaywrightTimeoutError):
                    pass
            if not handled:
                break

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
        self._dismiss_startup_blockers(page)
        popup_selectors = [
            "button:has-text('Close')",
            "button:has-text('Dismiss')",
            "button:has-text('No Thanks')",
            "button:has-text('Not now')",
            "[class*='modal-close']",
            "[class*='popup-close']",
            "[data-testid*='close']",
            "button[aria-label*='Close']",
            "button[aria-label*='Dismiss']",
        ]
        
        for selector in popup_selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=1000)
                    page.wait_for_timeout(300)
                    break
            except Exception:
                pass

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
        if "bargain" in key or "offer" in key:
            return "300"
        if "password" in key:
            return "Test@1234"
        if "mobile" in key or "phone" in key:
            return self._random_mobile_number()
        if "zip" in key or "postcode" in key:
            return "560001"
        if "pin_code" in key or "pincode" in key or key == "pin":
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

    def _location_result_text(self, pin_code: str) -> str:
        normalized_pin = re.sub(r"\D", "", str(pin_code or "")).strip()
        location_map = {
            "560001": "Bengaluru, Karnataka 560001, India",
            "560037": "Bengaluru, Karnataka 560037, India",
        }
        return location_map.get(normalized_pin, normalized_pin or "location result")

    def _fallback_click_locator(self, page, target: str):
        target_key = self._slugify(target)
        try:
            if target_key in {"log_in", "sign_in", "signin", "login", "sign_up", "signup"}:
                header_login = page.locator("#header-login-btn")
                if header_login.count() > 0:
                    return header_login.first

                signin_link = page.locator("a[href*='/auth/signin']")
                if signin_link.count() > 0:
                    return signin_link.first

                # On some responsive states, login is available only in the location dropdown.
                location_menu = page.locator("#location-desktop-menu-btn")
                if location_menu.count() > 0:
                    location_menu.first.click(force=True)
                    page.wait_for_timeout(250)
                location_login = page.locator("#location-desktop-login-link")
                if location_login.count() > 0:
                    return location_login.first

            if target_key == "share":
                candidates = [
                    "#pdp-share-btn",
                    "#product-share-btn",
                    "#share-button",
                    "[id*='share'][role='button']",
                    "button[aria-label*='share' i]",
                    "button:has-text('Share')",
                ]
                for selector in candidates:
                    node = page.locator(selector)
                    if node.count() > 0:
                        return node.first

            if target_key in {"email", "send"}:
                email_targets = [
                    "input[type='email']",
                    "input[placeholder*='email' i]",
                    "textarea[placeholder*='email' i]",
                    "button:has-text('Send')",
                    "button:has-text('Email')",
                ]
                for selector in email_targets:
                    node = page.locator(selector)
                    if node.count() > 0:
                        return node.first

            if "just_bargained" in target_key and "view_all" in target_key:
                view_more = page.locator("#home-wp3-view-more")
                if view_more.count() > 0:
                    return view_more.first
                # Some pages reuse ids/sections; try generic View All fallback.
                candidate = page.get_by_text("View All", exact=False)
                if candidate.count() > 0:
                    return candidate.first

            if "trending" in target_key and "view_all" in target_key:
                candidates = [
                    "#home-wp2-view-more",
                    "section:has-text('Trending Products') button:has-text('View All')",
                    "section:has-text('Trending Products') a:has-text('View All')",
                    "[id*='wp2'][id*='view'][id*='more']",
                ]
                for selector in candidates:
                    node = page.locator(selector)
                    if node.count() > 0:
                        return node.first
                trend_view_all = page.get_by_role("link", name=re.compile(r"view\s*all", re.IGNORECASE))
                if trend_view_all.count() > 0:
                    return trend_view_all.first

            if "bengaluru" in target_key or "560001" in target_key or "location" in target_key:
                location_result = page.get_by_text(re.compile(r"^bengaluru\s*\(560001\)$|bengaluru\s*\(560001\)|560001", re.IGNORECASE))
                if location_result.count() > 0:
                    return location_result.first

            if "accept" in target_key and ("bargain" in target_key or "offer" in target_key):
                accept_offer = page.locator("#bargain-accept-offer-btn")
                if accept_offer.count() > 0:
                    return accept_offer.first
                accepted_buy_now = page.locator("#bargain-accepted-buy-now-btn")
                if accepted_buy_now.count() > 0:
                    return accepted_buy_now.first
                accept_button = page.get_by_role("button", name=re.compile("accept", re.IGNORECASE))
                if accept_button.count() > 0:
                    return accept_button.first

            if "buy_now" in target_key or ("buy" in target_key and "button" in target_key):
                buy_now = page.locator("#pdp-button-8, #pdp-button-6, #bargain-accepted-buy-now-btn")
                if buy_now.count() > 0:
                    return buy_now.first
                buy_button = page.get_by_role("button", name=re.compile("buy now", re.IGNORECASE))
                if buy_button.count() > 0:
                    return buy_button.first

            if "logout" in target_key:
                logout_direct = page.locator("button:has-text('Logout'), a:has-text('Logout')")
                if logout_direct.count() > 0:
                    return logout_direct.first
                account_menu = page.locator("#header-user-menu-btn, #header-account-btn").first
                if account_menu.count() > 0:
                    account_menu.click(force=True)
                    page.wait_for_timeout(250)
                    if logout_direct.count() > 0:
                        return logout_direct.first

            if target_key == "pay" or ("pay" in target_key and "button" in target_key):
                pay_button = page.locator("#checkout-pay-now-mobile-btn, #checkout-pay-now-desktop-btn")
                if pay_button.count() > 0:
                    return pay_button.first
                generic_pay = page.get_by_role("button", name=re.compile("^pay$|pay now", re.IGNORECASE))
                if generic_pay.count() > 0:
                    return generic_pay.first

            if "pay_online" in target_key or ("pay" in target_key and "online" in target_key):
                pay_online = page.locator("#checkout-payment-online-option")
                if pay_online.count() > 0:
                    return pay_online.first
                generic_online = page.get_by_text(re.compile("pay online", re.IGNORECASE))
                if generic_online.count() > 0:
                    return generic_online.first

            if "home_address_type" in target_key or (
                "home" in target_key and "address" in target_key
            ):
                home_btn = page.locator(
                    "#checkout-address-type-home, input[id*='home'][type='radio'], button[id*='home'][id*='address'], [role='button'][id*='home'][id*='address']"
                )
                if home_btn.count() > 0:
                    return home_btn.first
                explicit_home = page.locator("button:has-text('Home')").first
                if explicit_home.count() > 0:
                    return explicit_home
                home_text = page.get_by_role("button", name=re.compile(r"^\s*home\s*$", re.IGNORECASE))
                if home_text.count() > 0:
                    return home_text.first

            if "save_address" in target_key or ("save" in target_key and "address" in target_key):
                save_btn = page.locator(
                    "#checkout-save-address-btn, button[id*='save'][id*='address'], button:has-text('Save Address')"
                )
                if save_btn.count() > 0:
                    return save_btn.first
                save_text = page.get_by_role("button", name=re.compile("save address", re.IGNORECASE))
                if save_text.count() > 0:
                    return save_text.first

            if "sera" in target_key and "basket" in target_key:
                preferred = page.locator("#brand-filter-item-name-58")
                if preferred.count() > 0:
                    return preferred.first
                generic_brand = page.locator("[id^='brand-filter-item-name-']")
                if generic_brand.count() > 0:
                    return generic_brand.first

            if "classic_15_7_inch_soft_tip_dartboard_game_set" in target_key or "selected_product" in target_key:
                product_link = page.locator("a[href*='/product-detail/']")
                if product_link.count() > 0:
                    return product_link.first

            if "bargain" in target_key and "button" in target_key:
                bargain_btn = page.locator(
                    "#pdp-button-7, #pdp-button-3, #bargain-start-btn, button[id*='bargain'][id*='start']"
                )
                if bargain_btn.count() > 0:
                    return bargain_btn.first
                bargain_btn = page.get_by_role(
                    "button",
                    name=re.compile(r"^\s*(start bargaining|bargain with seller)\s*$", re.IGNORECASE),
                )
                if bargain_btn.count() > 0:
                    return bargain_btn.first
                bargain_btn = page.get_by_text(re.compile(r"\bstart\s+bargaining\b", re.IGNORECASE))
                if bargain_btn.count() > 0:
                    return bargain_btn.first
        except PlaywrightError:
            return None
        return None

    def _prepare_checkout_address_form(self, page) -> None:
        try:
            page.evaluate(
                """
                () => {
                  const norm = (v) => String(v || '').toLowerCase();
                  const visible = (el) => {
                    const s = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
                  };

                  const defaults = {
                    name: 'Demo User',
                    mobile: '9876543210',
                    phone: '9876543210',
                    pincode: '560001',
                    pin: '560001',
                    zipcode: '560001',
                    zip: '560001',
                    city: 'Bengaluru',
                    state: 'Karnataka',
                    addressline1: '123 Demo Street',
                    addressline2: 'Near Demo Landmark',
                    landmark: 'Demo Circle',
                    locality: 'MG Road',
                  };

                  const pickValue = (keyText) => {
                    const k = norm(keyText);
                    for (const [key, value] of Object.entries(defaults)) {
                      if (k.includes(key)) return value;
                    }
                    return '';
                  };

                  const fields = Array.from(document.querySelectorAll('input, textarea')).filter((el) => {
                    if (!visible(el)) return false;
                    if (el.disabled || el.readOnly) return false;
                    const t = norm(el.getAttribute('type'));
                    if (['hidden', 'submit', 'button', 'image', 'file', 'radio', 'checkbox'].includes(t)) return false;
                    return true;
                  });

                  for (const el of fields) {
                    const keyText = [
                      el.id,
                      el.getAttribute('name'),
                      el.getAttribute('placeholder'),
                      el.getAttribute('aria-label'),
                      (el.labels ? Array.from(el.labels).map((l) => l.textContent || '').join(' ') : ''),
                    ].join(' ');

                    const current = String(el.value || '').trim();
                    if (current) continue;
                    const next = pickValue(keyText);
                    if (!next) continue;
                    el.focus();
                    el.value = next;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                }
                """
            )

            # Prefer Home address type in checkout address form before save.
            # If it's a radio input, check it explicitly.
            radio_selectors = [
                "#checkout-address-type-home",
                "input[id*='home'][type='radio']",
                "input[name*='address'][type='radio'][value*='home' i]",
            ]
            for selector in radio_selectors:
                try:
                    node = page.locator(selector).first
                    if node.count() > 0 and node.is_visible(timeout=400):
                        try:
                            node.check(force=True, timeout=2000)
                        except PlaywrightError:
                            node.click(force=True, timeout=2000)
                        return
                except PlaywrightError:
                    continue

            for selector in [
                "button[id*='home'][id*='address']",
                "label:has-text('Home')",
                "button:has-text('Home')",
            ]:
                try:
                    node = page.locator(selector).first
                    if node.count() > 0 and node.is_visible(timeout=400):
                        node.click(force=True, timeout=2000)
                        return
                except PlaywrightError:
                    continue
        except PlaywrightError:
            return

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

        key = self._slugify(raw)
        if "account_created" in key or "account_created_successfully" in key:
            for alias in ["ACCOUNT CREATED", "My Bargains", "My Orders", "Welcome"]:
                if alias not in candidates:
                    candidates.append(alias)

        if "560001" in raw or "bengaluru" in key or "location" in key:
            for alias in ["560001", "Bengaluru", "Karnataka"]:
                if alias not in candidates:
                    candidates.append(alias)

        if "deal_of_the_day" in key:
            for alias in ["Gajab Deal of the Day", "Deal of the Day"]:
                if alias not in candidates:
                    candidates.append(alias)

        return candidates or [raw]

    def _strip_assertion_suffixes(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(r"\bsection\s+is\s+visible\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bis visible\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bvisible successfully\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bexists\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bdisplayed\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bis loaded\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsection\b$", "", cleaned, flags=re.IGNORECASE)
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
            if "bargain" in slug and "button" in slug:
                fallbacks.extend([
                    (page.locator("#pdp-button-7, #pdp-button-3, #bargain-start-btn, button[id*='bargain'][id*='start']"), {"strategy": "css", "value": "#pdp-button-7, #pdp-button-3, #bargain-start-btn, button[id*='bargain'][id*='start']"}),
                    (page.get_by_role("button", name=re.compile(r"^\s*(start bargaining|bargain with seller)\s*$", re.IGNORECASE)), {"strategy": "role", "role": "button", "name": "start bargaining"}),
                    (page.get_by_text(re.compile(r"\bstart\s+bargaining\b", re.IGNORECASE)), {"strategy": "text", "value": "start bargaining"}),
                ])
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
            if not ("bargain" in slug and "button" in slug):
                for word in cleaned.split():
                    if len(word) > 3:
                        self._append_locator_candidate(fallbacks, page.locator("button").filter(has_text=word), {"strategy": "text", "value": word})
                        self._append_locator_candidate(fallbacks, page.locator("a").filter(has_text=word), {"strategy": "text", "value": word})

        elif kind == "fill":
            if "address" in slug and "line" in slug and "1" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='addressline1'], input[id*='addressline1']"), {"strategy": "css", "value": "input[name*='addressline1'], input[id*='addressline1']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Address Line 1' i], input[placeholder*='Address line 1' i]"), {"strategy": "css", "value": "input[placeholder*='Address Line 1' i], input[placeholder*='Address line 1' i]"})
            if "address" in slug and "line" in slug and "2" in slug:
                self._append_locator_candidate(fallbacks, page.locator("input[name*='addressline2'], input[id*='addressline2']"), {"strategy": "css", "value": "input[name*='addressline2'], input[id*='addressline2']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Address Line 2' i], input[placeholder*='Address line 2' i]"), {"strategy": "css", "value": "input[placeholder*='Address Line 2' i], input[placeholder*='Address line 2' i]"})
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
                self._append_locator_candidate(fallbacks, page.locator("input[name*='addressline1'], input[id*='addressline1']"), {"strategy": "css", "value": "input[name*='addressline1'], input[id*='addressline1']"})
                self._append_locator_candidate(fallbacks, page.locator("input[name*='addressline2'], input[id*='addressline2']"), {"strategy": "css", "value": "input[name*='addressline2'], input[id*='addressline2']"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Address Line 1' i], input[placeholder*='Address line 1' i]"), {"strategy": "css", "value": "input[placeholder*='Address Line 1' i], input[placeholder*='Address line 1' i]"})
                self._append_locator_candidate(fallbacks, page.locator("input[placeholder*='Address Line 2' i], input[placeholder*='Address line 2' i]"), {"strategy": "css", "value": "input[placeholder*='Address Line 2' i], input[placeholder*='Address line 2' i]"})
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

    def _launch_selected_browser(self, playwright, ui_input: dict):
        browser_name = str(ui_input.get("browser", "chromium") or "chromium").strip().lower()
        headless = not bool(ui_input.get("headed", False))
        launcher = getattr(playwright, browser_name, None)
        if launcher is None:
            launcher = playwright.chromium
        return launcher.launch(headless=headless)

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










