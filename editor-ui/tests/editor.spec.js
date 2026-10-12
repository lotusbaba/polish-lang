import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, copyFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
let child, directory, file, url;
test.beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'polish-ui-'));
  file = join(directory, 'cockroachdb.polishd');
  await copyFile('../examples/cockroachdb.polishd', file);
  child = spawn(resolve('../.venv/bin/python'), ['-m', 'polish', 'editor', file, '--no-browser', '--port', '0'], { cwd: resolve('..') });
  url = await new Promise((resolve, reject) => {
    child.stdout.on('data', data => { const match = data.toString().match(/Polish editor: (\S+)/); if (match) resolve(match[1]); });
    child.on('error', reject);
    child.on('exit', code => reject(new Error(`Server exited: ${code}`)));
  });
});
test.afterAll(async () => { child?.kill(); await rm(directory, { recursive: true, force: true }); });
test('renders architecture, simulates, validates, and saves its source', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { delete window.showSaveFilePicker; delete window.showOpenFilePicker; delete window.showDirectoryPicker; });
  await page.goto(url);
  await expect(page.getByRole('status')).toContainText('components');
  await expect(page.locator('canvas').first()).toBeVisible();
  await page.screenshot({ path: '/tmp/polish-editor.png' });
  await page.getByRole('button', { name: 'Simulate', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('5/5 scenarios passed');
  const source = page.getByRole('textbox', { name: 'Polish specification' });
  const original = await source.inputValue();
  await source.fill('architecture Broken {');
  await page.getByRole('button', { name: 'Check & draw', exact: true }).click();
  await expect(page.getByLabel('Validation results')).toContainText('Review required');
  await source.fill(original + '\n// Edited in Polish Studio\n');
  await page.getByRole('button', { name: 'Save file', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved cockroachdb.polishd');
  expect(await readFile(file, 'utf8')).toContain('// Edited in Polish Studio');
  await page.getByTestId('main-menu-trigger').click();
  await page.getByRole('button', { name: 'Save to...', exact: true }).click();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Save to file', exact: true }).click();
  const scene = JSON.parse(await readFile(await (await downloaded).path(), 'utf8'));
  expect(scene.elements.filter(e => e.type === 'rectangle' && e.customData?.polish)).toHaveLength(11);
  expect(scene.elements.filter(e => e.type === 'arrow')).toHaveLength(11);
  expect(scene.elements.find(e => e.id === 'polish:API')).toBeTruthy();
  expect(errors).toEqual([]);
});

test('editing a canvas label updates the specification and saves the rename', async ({ page }) => {
  await page.goto(url);
  await expect(page.getByRole('status')).toContainText('components');
  // Center the component using the same navigation control available to users.
  await page.getByLabel('Jump to component').selectOption('API');
  await page.mouse.dblclick(905, 547);
  const label = page.locator('textarea.excalidraw-wysiwyg');
  await expect(label).toBeVisible();
  await label.fill('Catalog\nservice');
  await label.press('Escape');
  const source = page.getByRole('textbox', { name: 'Polish specification' });
  await expect(source).toHaveValue(/service Catalog/);
  await expect(source).toHaveValue(/entry = Catalog/);
  await page.keyboard.press('ControlOrMeta+z');
  await expect(source).toHaveValue(/service API/);
  await page.keyboard.press('ControlOrMeta+Shift+z');
  await expect(source).toHaveValue(/service Catalog/);
  await page.getByRole('button', { name: 'Save file', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved cockroachdb.polishd');
  expect(await readFile(file, 'utf8')).toContain('service Catalog');
});

test('a newly drawn component updates source and deletion removes it', async ({ page }) => {
  await page.goto(url);
  await expect(page.getByRole('status')).toContainText('components');
  await page.mouse.click(420, 690);
  await page.keyboard.press('r');
  await page.mouse.move(1280, 500); await page.mouse.down();
  await page.mouse.move(1410, 580); await page.mouse.up();
  await page.mouse.dblclick(1345, 540);
  await page.screenshot({ path: '/tmp/polish-newnode.png' });
  const label = page.locator('textarea.excalidraw-wysiwyg');
  await expect(label).toBeVisible();
  await label.fill('role Observer');
  await label.press('Escape');
  const source = page.getByRole('textbox', { name: 'Polish specification' });
  await expect(source).toHaveValue(/role Observer/);
  // Delete the new shape through the canvas, not through the source pane.
  await page.mouse.click(1281, 510);
  await page.keyboard.press('Backspace');
  await expect(source).not.toHaveValue(/role Observer/);
});
