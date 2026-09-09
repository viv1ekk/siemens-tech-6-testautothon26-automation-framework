/**
 * Playwright configuration for generated UI tests.
 * Keeps headed browser sessions maximized and lets Playwright use the
 * browser window size instead of a fixed viewport.
 */

/** @type {import('@playwright/test').PlaywrightTestConfig} */
module.exports = {
  use: {
    viewport: null,
    launchOptions: {
      args: ['--start-maximized'],
    },
  },
};