import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const python = process.env.WB_PYTHON || (process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
export default defineConfig({
  testDir: './web-tests', fullyParallel: false, workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:18765', viewport: { width: 1440, height: 1000 }, launchOptions: { channel: process.env.WB_BROWSER_CHANNEL || undefined }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: `"${python}" -m workbench run --port 18765`, url: 'http://127.0.0.1:18765/api/health', reuseExistingServer: false, timeout: 30000, env: { ELEPHANT_WORKBENCH_DATA: join(tmpdir(), 'elephant-e2e-' + Date.now()) } },
});
