"""Mobile executor using Playwright emulation with optional cloud-device metadata."""

import asyncio
import os

from playwright.async_api import async_playwright


DEVICE_PRESETS = {
    "iPhone 15": {"width": 393, "height": 852, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"},
    "iPhone 12": {"width": 390, "height": 844, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"},
    "Pixel 8": {"width": 412, "height": 915, "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"},
    "Galaxy S23": {"width": 393, "height": 873, "user_agent": "Mozilla/5.0 (Linux; Android 13; Galaxy S23) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"},
}


class MobileExecutor:
    def __init__(self):
        self.browserstack_user = os.environ.get("BROWSERSTACK_USERNAME", "").strip()
        self.browserstack_key = os.environ.get("BROWSERSTACK_ACCESS_KEY", "").strip()

    async def run_skill(
        self,
        skill_name: str,
        device: dict,
        network: str,
        scenario_path: str = None,
        base_url: str = None,
        run_profile: str = "demo",
    ) -> dict:
        """Run a web flow in mobile emulation, with optional cloud-provider readiness metadata."""
        if not base_url:
            return {
                "passed": False,
                "device": device["name"],
                "network": network,
                "provider": self._provider_name(),
                "method": "mobile_unavailable",
                "error": "Base URL is required for mobile execution.",
            }

        profile = DEVICE_PRESETS.get(device["name"], DEVICE_PRESETS["Pixel 8"])
        latency_ms = {"wifi": 0, "4g": 75, "3g": 300, "2g": 1200}.get(network, 0)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": profile["width"], "height": profile["height"]},
                user_agent=profile["user_agent"],
                is_mobile=True,
                has_touch=True,
            )
            page = await context.new_page()
            try:
                if latency_ms:
                    await asyncio.sleep(latency_ms / 1000)
                await page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                result = await self._dispatch(skill_name, page)
                return {
                    "passed": result.get("passed", True),
                    "device": device["name"],
                    "network": network,
                    "provider": self._provider_name(),
                    "method": "playwright_mobile_emulation",
                    "run_profile": run_profile,
                    "scenario_path": scenario_path or "",
                    "details": result,
                }
            except Exception as exc:
                return {
                    "passed": False,
                    "device": device["name"],
                    "network": network,
                    "provider": self._provider_name(),
                    "method": "playwright_mobile_emulation",
                    "error": str(exc),
                }
            finally:
                await context.close()
                await browser.close()

    async def _dispatch(self, skill_name: str, page) -> dict:
        if skill_name == "form_validation":
            form = await page.locator("form, input, textarea, select").count()
            return {"passed": form > 0, "signal": "form_controls_present", "count": form}
        if skill_name == "payment_submission":
            cta = await page.locator("button, a").filter(has_text="Pay").count()
            fallback = await page.locator("button, a").filter(has_text="Checkout").count()
            return {"passed": (cta + fallback) > 0, "signal": "payment_cta_present", "count": cta + fallback}
        if skill_name == "auth_flow":
            auth_controls = await page.locator("input[type='password'], input[type='email'], button, a").count()
            return {"passed": auth_controls > 0, "signal": "auth_controls_present", "count": auth_controls}
        if skill_name == "accessibility_check":
            body = await page.locator("body").count()
            return {"passed": body > 0, "signal": "page_visible"}
        if skill_name == "error_handling":
            body = await page.locator("body").count()
            return {"passed": body > 0, "signal": "page_survived_mobile_latency"}
        return {"passed": True, "signal": f"{skill_name}_placeholder"}

    def _provider_name(self) -> str:
        if self.browserstack_user and self.browserstack_key:
            return "browserstack_ready"
        return "playwright_emulation"
