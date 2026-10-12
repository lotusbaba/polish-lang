import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

let child, directory, url, original;
test.beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'polish-layout-'));
  original = process.env.POLISH_EDITOR_TEST_SPEC ? await readFile(process.env.POLISH_EDITOR_TEST_SPEC, 'utf8') :
    `architecture Large { compute_pool Host { instances = 1 } service API { hosted_on Host ${Array.from({length:41},(_,i)=>`operation op${i} {}`).join('\n')} } ${Array.from({length:67},(_,i)=>`role User${i}`).join('\n')} }`;
  const file = join(directory, 'large.polishd'); await writeFile(file, original);
  child = spawn(resolve('../.venv/bin/python'), ['-m', 'polish', 'editor', file, '--no-browser', '--port', '0'], { cwd: resolve('..') });
  url = await new Promise((resolve, reject) => {
    child.stdout.on('data', data => { const match = data.toString().match(/Polish editor: (\S+)/); if (match) resolve(match[1]); });
    child.on('error', reject); child.on('exit', code => reject(new Error(`Server exited: ${code}`)));
  });
});
test.afterAll(async () => { child?.kill(); await rm(directory, {recursive:true, force:true}); });
test('large diagrams open legibly and offer overview, navigation, and layout controls', async ({ page }) => {
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  await page.goto(url);
  await expect(page.getByRole('status')).toContainText('110 components');
  await expect(page.getByRole('button', {name:'Reset zoom', exact:true})).toHaveText('85%');
  await expect(page.getByLabel('Diagram layout')).toHaveValue('balanced');
  const source=page.getByRole('textbox', {name:'Polish specification'});
  await expect(source).toHaveValue(original);
  await page.getByRole('button', {name:'Hide source',exact:true}).click();
  await page.getByRole('button', {name:'Readable zoom',exact:true}).click();
  await page.screenshot({path:'/tmp/polish-large-readable.png'});
  await page.getByRole('button', {name:'Fit canvas',exact:true}).click();
  await expect(page.getByRole('button', {name:'Reset zoom',exact:true})).not.toHaveText('85%');
  await page.screenshot({path:'/tmp/polish-large-overview.png'});
  await page.getByLabel('Jump to component').selectOption({index:2});
  await expect(page.getByRole('button', {name:'Reset zoom',exact:true})).toHaveText('85%');
  await page.getByLabel('Diagram layout').selectOption('vertical');
  await expect(page.getByRole('status')).toContainText('110 components');
  await page.getByRole('button', {name:'Show source',exact:true}).click();
  await expect(source).toHaveValue(original);
  expect(errors).toEqual([]);
});
