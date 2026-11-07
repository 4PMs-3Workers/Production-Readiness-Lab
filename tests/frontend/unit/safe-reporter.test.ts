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
      {
        id: secret,
        title: secret,
        parent: { project: () => ({ name: secret }) },
        location: { file: secret, line: 0, column: 0 },
      } as unknown as TestCase,
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
      attempts: [
        {
          index: 1,
          caseId: expect.stringMatching(/^unknown:0:0:[a-f0-9]{64}$/),
          project: 'unknown',
          status: 'failed',
          retry: 0,
        },
      ],
    });
    expect(readdirSync(directory)).toEqual(['summary.json']);
    expect(readFileSync(outputFile, 'utf8')).not.toContain(secret);
    expect(stdout.mock.calls.flat().join('')).not.toContain(secret);
  } finally {
    stdout.mockRestore();
    rmSync(directory, { recursive: true, force: true });
  }
});

it('identifies cases/projects independently of completion order and retries', () => {
  const directory = mkdtempSync(join(tmpdir(), 'safe-case-'));
  const stdout = vi.spyOn(process.stdout, 'write').mockReturnValue(true);
  const testCase = (id: string, project: string) =>
    ({
      id,
      title: 'never serialized',
      location: { file: '/repo/tests/frontend/api-e2e/admin-portal.spec.ts', line: 83, column: 5 },
      parent: { project: () => ({ name: project }) },
    }) as unknown as TestCase;
  const a = testCase('case-a', 'api-desktop'),
    b = testCase('case-b', 'api-mobile');
  const result = (retry: number) => ({ status: 'failed', retry }) as TestResult;
  try {
    const run = (name: string, cases: TestCase[]) => {
      const outputFile = join(directory, name);
      const reporter = new SafeReporter({ outputFile });
      cases.forEach((test, retry) => reporter.onTestEnd(test, result(retry)));
      reporter.onEnd({ status: 'failed' } as FullResult);
      return JSON.parse(readFileSync(outputFile, 'utf8')).attempts;
    };
    const first = run('first.json', [a, b, a]),
      second = run('second.json', [b, a]);
    expect(first[0].caseId).toBe(first[2].caseId);
    expect(first[0].caseId).toBe(second[1].caseId);
    expect(first[1].caseId).toBe(second[0].caseId);
    expect(first[0].caseId).not.toBe(first[1].caseId);
    expect(first[0].caseId).toMatch(/^admin-portal\.spec\.ts:83:5:[a-f0-9]{64}$/);
    expect(first.map((x: { project: string }) => x.project)).toEqual([
      'api-desktop',
      'api-mobile',
      'api-desktop',
    ]);
    expect(stdout.mock.calls.flat().join('')).toContain(first[0].caseId);
    expect(readFileSync(join(directory, 'first.json'), 'utf8')).not.toContain('never serialized');
  } finally {
    stdout.mockRestore();
    rmSync(directory, { recursive: true, force: true });
  }
});
