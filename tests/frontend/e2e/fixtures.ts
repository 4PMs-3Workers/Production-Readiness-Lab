import { expect, test as base } from '@playwright/test';

export { expect };

export const test = base.extend<{ runtimeErrors: void }>({
  runtimeErrors: [
    async ({ page }, runTest) => {
      const errors: string[] = [];
      const recordError = (error: Error) => errors.push(error.message);
      page.on('pageerror', recordError);
      try {
        await runTest();
      } finally {
        page.off('pageerror', recordError);
        expect.soft(errors, 'No uncaught browser errors during the demo flow').toEqual([]);
      }
    },
    { auto: true },
  ],
});
