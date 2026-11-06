import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { createHash } from 'node:crypto';
import type {
  FullResult,
  Reporter,
  TestCase,
  TestError,
  TestResult,
} from '@playwright/test/reporter';

const projects = new Set(['api-desktop', 'api-mobile', 'desktop-chromium', 'mobile-chromium']);
function reference(test: TestCase) {
  const file = test.location?.file.replace(/\\/g, '/').split('/').pop() ?? '';
  const safeFile = /^[a-z0-9-]+\.spec\.ts$/.test(file) ? file : 'unknown';
  const line =
    Number.isSafeInteger(test.location?.line) && test.location.line > 0 ? test.location.line : 0;
  const column =
    Number.isSafeInteger(test.location?.column) && test.location.column > 0
      ? test.location.column
      : 0;
  const digest = createHash('sha256').update(test.id).digest('hex');
  const project = test.parent.project()?.name ?? '';
  return {
    caseId: `${safeFile}:${line}:${column}:${digest}`,
    project: projects.has(project) ? project : 'unknown',
  };
}

/** CI evidence deliberately contains no titles, steps, errors, output or attachments. */
export default class SafeReporter implements Reporter {
  private attempts: {
    index: number;
    caseId: string;
    project: string;
    status: TestResult['status'];
    retry: number;
  }[] = [];
  private globalErrors = 0;
  constructor(private options: { outputFile: string }) {}
  printsToStdio() {
    return true;
  }
  onStdOut(chunk: string | Buffer) {
    void chunk;
  }
  onStdErr(chunk: string | Buffer) {
    void chunk;
  }
  onError(error: TestError) {
    void error;
    this.globalErrors++;
  }
  onTestEnd(test: TestCase, result: TestResult) {
    const identity = reference(test);
    this.attempts.push({
      index: this.attempts.length + 1,
      ...identity,
      status: result.status,
      retry: result.retry,
    });
    process.stdout.write(
      `E2E ${identity.caseId} project=${identity.project} retry=${result.retry}: ${result.status}\n`,
    );
  }
  onEnd(result: FullResult) {
    const summary = {
      status: result.status,
      globalErrors: this.globalErrors,
      attempts: this.attempts,
    };
    mkdirSync(dirname(this.options.outputFile), { recursive: true });
    writeFileSync(this.options.outputFile, JSON.stringify(summary, null, 2) + '\n');
    process.stdout.write(
      `E2E: ${result.status}; attempts=${this.attempts.length}; globalErrors=${this.globalErrors}\n`,
    );
  }
}
