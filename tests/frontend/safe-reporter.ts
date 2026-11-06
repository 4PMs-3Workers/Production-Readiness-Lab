import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type {
  FullResult,
  Reporter,
  TestCase,
  TestError,
  TestResult,
} from '@playwright/test/reporter';

/** CI evidence deliberately contains no titles, steps, errors, output or attachments. */
export default class SafeReporter implements Reporter {
  private attempts: { index: number; status: TestResult['status']; retry: number }[] = [];
  private globalErrors = 0;
  constructor(private options: { outputFile: string }) {}
  printsToStdio() {
    return true;
  }
  onStdOut(_chunk: string | Buffer) {}
  onStdErr(_chunk: string | Buffer) {}
  onError(_error: TestError) {
    this.globalErrors++;
  }
  onTestEnd(_test: TestCase, result: TestResult) {
    this.attempts.push({
      index: this.attempts.length + 1,
      status: result.status,
      retry: result.retry,
    });
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
