"""Generated page methods."""

import { Page } from '@playwright/test';
import { locators, LocatorKey } from './elements';

export class UiSmokePageMethods {
  constructor(private page: Page) {}

  private resolve(key: LocatorKey) {
    const locator = locators[key];
    switch (locator.strategy) {
      case 'css': {
        const resolved = this.page.locator(locator.value);
        return 'nth' in locator ? resolved.nth(locator.nth) : resolved;
      }
      case 'label':
        return this.page.getByLabel(locator.value);
      case 'placeholder':
        return this.page.getByPlaceholder(locator.value);
      case 'role':
        return this.page.getByRole(locator.value.role, { name: locator.value.name });
      case 'text':
        return this.page.getByText(locator.value);
      default:
        throw new Error(`Unsupported locator strategy: ${locator.strategy}`);
    }
  }

  async fill(key: LocatorKey, value: string) {
    await this.resolve(key).fill(value);
  }

  async select(key: LocatorKey, index = 0) {
    await this.resolve(key).selectOption({ index });
  }

  async click(key: LocatorKey) {
    await this.resolve(key).first().click();
  }

  async hover(key: LocatorKey) {
    const target = this.resolve(key).first();
    await target.hover({ force: true });
    await target.dispatchEvent('mouseover');
    await target.dispatchEvent('mouseenter');
  }

  async text(key: LocatorKey) {
    return await this.resolve(key).innerText();
  }
}
