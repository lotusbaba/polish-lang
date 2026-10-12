import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './tests', workers: 1, use: { viewport: { width: 1440, height: 1000 } } });
