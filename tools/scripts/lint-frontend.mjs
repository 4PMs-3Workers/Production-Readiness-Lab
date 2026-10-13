import { ESLint } from 'eslint';
import { fileURLToPath } from 'node:url';

// Resolve tests from the repository root even when npm runs in the workspace.
const repositoryRoot = fileURLToPath(new URL('../..', import.meta.url));
const eslint = new ESLint({ cwd: repositoryRoot });
const results = await eslint.lintFiles(['src/frontend', 'tests/frontend']);
const formatter = await eslint.loadFormatter('stylish');
const output = formatter.format(results);

if (output) process.stdout.write(output);
if (results.some((result) => result.errorCount > 0 || result.warningCount > 0))
  process.exitCode = 1;
