import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('../..', import.meta.url)),
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('.', import.meta.url)) },
  },
  test: {
    allowOnly: !process.env.CI,
    environment: 'jsdom',
    setupFiles: ['tests/frontend/setup.ts'],
    include: ['tests/frontend/{unit,components}/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
