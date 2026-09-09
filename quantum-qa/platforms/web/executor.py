"""Web test executor using Playwright."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from platforms.web.test_runner import PlaywrightTestRunner


class WebExecutor:
    """Executes generated Playwright tests against web applications."""
    
    def __init__(self):
        self.artifacts_dir = Path("artifacts/ui_generated")
        self.test_runner = PlaywrightTestRunner()
    
    async def run_skill(self, skill_name: str, scenario_path: str = None, base_url: str = None) -> dict:
        """
        Run generated Playwright tests for a skill.
        
        Args:
            skill_name: Name of the skill (form_validation, payment_submission, etc.)
            scenario_path: Path to scenario folder or test file
        
        Returns:
            Dict with passed (bool), duration_s, details, etc.
        """
        try:
            # Find test files
            test_files = self._find_test_files(scenario_path)
            
            if not test_files:
                return {
                    "passed": False,
                    "error": "No generated tests found",
                    "method": "no_tests"
                }
            
            # Run the first test file found
            test_file = test_files[0]
            result = await self.test_runner.run_test_file(test_file)
            result["method"] = "playwright_generated"
            return result
            
        except Exception as e:
            return {
                "passed": False,
                "error": str(e),
                "duration_s": 0,
                "method": "error"
            }
    
    def _find_test_files(self, scenario_path: str = None) -> list:
        """Find generated test files."""
        if scenario_path:
            # If specific path provided, look there
            if Path(scenario_path).is_file():
                return [Path(scenario_path)]
            
            test_dir = Path(scenario_path) / "tests"
            if test_dir.exists():
                return list(test_dir.glob("test_*.spec.ts"))
        
        return []
    
    async def run_skill_inline(self, skill_name: str, base_url: str) -> dict:
        """
        Run a skill directly with Playwright (inline execution).
        Fallback for when generated tests aren't available.
        """
        import time
        start_time = time.time()
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(base_url or "https://example.com", timeout=10000)
                # Skill-specific logic dispatched here
                result = await self._dispatch(skill_name, page)
                return {
                    "passed": bool(result.get("passed", False)),
                    "duration_s": round(time.time() - start_time, 2),
                    "method": "inline",
                    **result
                }
            except Exception as e:
                return {
                    "passed": False,
                    "error": str(e),
                    "duration_s": round(time.time() - start_time, 2),
                    "method": "inline"
                }
            finally:
                await browser.close()

    async def _dispatch(self, skill: str, page) -> dict:
        """Skill-specific test implementation."""
        if skill == "form_validation":
            try:
                await page.wait_for_selector("form", timeout=5000)
                return {"passed": True, "action": "form_validation_checked", "skill": skill}
            except Exception:
                return {"passed": False, "action": skill, "status": "form_not_found", "error": "No form element found"}
        
        if skill == "payment_submission":
            try:
                await page.wait_for_selector("#pay-btn", timeout=5000)
                return {"passed": True, "action": "payment_button_found", "skill": skill}
            except Exception:
                return {"passed": False, "action": skill, "status": "button_not_found", "error": "Payment button not found"}
        
        if skill == "error_handling":
            try:
                # Test error handling by triggering an invalid action
                await page.goto("invalid://url", timeout=2000)
                return {"passed": False, "action": "error_handling_tested", "skill": skill, "error": "Invalid URL unexpectedly succeeded"}
            except Exception:
                return {"passed": True, "action": "error_handling_tested", "skill": skill}
        
        if skill == "auth_flow":
            try:
                password_field = await page.query_selector("input[type='password']")
                login_btn = await page.query_selector("button:has-text('Login'), button[type='submit'], input[type='submit']")
                passed = password_field is not None and login_btn is not None
                return {
                    "passed": passed,
                    "action": "auth_flow_detected",
                    "found_password_field": password_field is not None,
                    "found_login_action": login_btn is not None,
                    "skill": skill,
                    "error": "" if passed else "Auth controls were not fully detected",
                }
            except Exception:
                return {"passed": False, "action": "auth_flow", "status": "not_found", "error": "Failed to inspect auth controls"}

        if skill == "accessibility_check":
            try:
                findings = await page.evaluate(
                    """
                    () => {
                      const interactive = Array.from(document.querySelectorAll('button, a, input, select, textarea'));
                      const unnamed = interactive.filter((el) => {
                        const text = (el.innerText || el.textContent || '').trim();
                        const aria = (el.getAttribute('aria-label') || '').trim();
                        const placeholder = (el.getAttribute('placeholder') || '').trim();
                        const title = (el.getAttribute('title') || '').trim();
                        return !(text || aria || placeholder || title);
                      });
                      return { interactiveCount: interactive.length, unnamedCount: unnamed.length };
                    }
                    """
                )
                interactive_count = int(findings.get("interactiveCount", 0))
                unnamed_count = int(findings.get("unnamedCount", 0))
                passed = interactive_count > 0 and unnamed_count == 0
                return {
                    "passed": passed,
                    "action": "accessibility_checked",
                    "interactive_count": interactive_count,
                    "unnamed_interactive_count": unnamed_count,
                    "skill": skill,
                    "error": "" if passed else f"{unnamed_count} interactive elements missing accessible naming",
                }
            except Exception:
                return {"passed": False, "action": "accessibility_check", "status": "inspection_failed", "error": "Accessibility inspection failed"}
        
        return {"passed": False, "action": f"{skill}_executed", "status": "not_implemented", "error": f"Inline skill '{skill}' is not implemented"}
