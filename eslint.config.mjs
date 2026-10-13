import js from '@eslint/js';
import nextPlugin from '@next/eslint-plugin-next';
import { defineConfig, globalIgnores } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import { fileURLToPath } from 'node:url';
import tseslint from 'typescript-eslint';

export default defineConfig([
  globalIgnores(['**/.next/**', '**/node_modules/**', '**/next-env.d.ts', '**/*.tsbuildinfo']),
  {
    basePath: fileURLToPath(new URL('.', import.meta.url)),
    files: ['src/frontend/**/*.{ts,tsx}', 'tests/frontend/**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
  },
  {
    basePath: fileURLToPath(new URL('.', import.meta.url)),
    files: ['src/frontend/**/*.{ts,tsx}', 'tests/frontend/**/*.{ts,tsx}'],
    plugins: {
      '@next/next': nextPlugin,
      'react-hooks': reactHooks,
    },
    settings: {
      next: { rootDir: fileURLToPath(new URL('./src/frontend', import.meta.url)) },
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,
      ...reactHooks.configs.recommended.rules,
    },
  },
]);
