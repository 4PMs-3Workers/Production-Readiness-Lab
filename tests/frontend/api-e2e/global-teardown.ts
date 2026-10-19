import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

export default function teardown() {
  execFileSync(
    process.env.FRONTEND_E2E_PYTHON ?? 'python',
    ['tools/scripts/frontend_e2e_backend.py', 'cleanup'],
    {
      cwd: resolve(__dirname, '../../..'),
      env: { ...process.env, PYTHONPATH: 'src/backend' },
      stdio: 'inherit',
    },
  );
}
