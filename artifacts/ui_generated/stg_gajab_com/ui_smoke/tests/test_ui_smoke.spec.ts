import { test, expect } from '@playwright/test';
import { UiSmokePage } from "../pages/page";

test('ui-smoke', async ({ page }) => {
  page.setDefaultTimeout(30000);
  await page.goto("https://stg.gajab.com/", { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('body').waitFor({ state: 'visible', timeout: 10000 });
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2000);
  const ui = new UiSmokePage(page);
  await page.goto("", { waitUntil: 'domcontentloaded' });
  await page.goto("https://stg.gajab.com/", { waitUntil: 'domcontentloaded' });
  await expect(page.getByText("home page is visible", { exact: false })).toBeVisible();
  await ui.click("header_logo_link");
  await expect(page).toHaveURL(new RegExp("signin"));
  await expect(page.getByText("Verify location results are displayed", { exact: false })).toBeVisible();
  await expect(page.getByText("gajab deal of the day section is visible", { exact: false })).toBeVisible();
  await page.goto("", { waitUntil: 'domcontentloaded' });
  await expect(page.getByText("Verify order details are displayed", { exact: false })).toBeVisible();
  await page.goto("", { waitUntil: 'domcontentloaded' });
  await expect(page.getByText("Verify the savings displayed", { exact: false })).toBeVisible();
  await expect(page.locator('body')).toBeVisible();
});
