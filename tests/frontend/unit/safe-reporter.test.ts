// @vitest-environment node
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import type { FullResult, TestCase, TestResult } from '@playwright/test/reporter';
import SafeReporter from '../safe-reporter';

it('allowlists CI evidence even when a failed test embeds secrets in every diagnostic surface', () => {
  const directory = mkdtempSync(join(tmpdir(), 'safe-e2e-'));
  const outputFile = join(directory, 'summary.json');
  const stdout = vi.spyOn(process.stdout, 'write').mockReturnValue(true);
  const secret = 'arbitrary-sensitive-input-🔐';
  try {
    const reporter = new SafeReporter({ outputFile });
    reporter.onStdOut(secret);
    reporter.onStdErr(Buffer.from(secret));
    reporter.onError({ message: secret, stack: secret });
    reporter.onTestEnd(
      { title: secret } as TestCase,
      {
        status: 'failed',
        retry: 0,
        error: { message: secret },
        errors: [{ message: secret }],
        stdout: [secret],
        stderr: [secret],
        steps: [{ title: `Fill ${secret}` }],
        attachments: [{ name: secret, path: secret, body: Buffer.from(secret) }],
      } as unknown as TestResult,
    );
    reporter.onEnd({ status: 'failed' } as FullResult);
    expect(JSON.parse(readFileSync(outputFile, 'utf8'))).toEqual({
      status: 'failed',
      globalErrors: 1,
      attempts: [{ index: 1, status: 'failed', retry: 0 }],
    });
    expect(readdirSync(directory)).toEqual(['summary.json']);
    expect(readFileSync(outputFile, 'utf8')).not.toContain(secret);
    expect(stdout.mock.calls.flat().join('')).not.toContain(secret);
  } finally {
    stdout.mockRestore();
    rmSync(directory, { recursive: true, force: true });
  }
});
