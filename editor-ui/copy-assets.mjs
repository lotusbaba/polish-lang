import { cp, copyFile } from 'node:fs/promises';
await cp('node_modules/@excalidraw/excalidraw/dist/prod/fonts', '../polish/editor_static/fonts', { recursive: true });
await copyFile('licenses/EXCALIDRAW-LICENSE.txt', '../polish/editor_static/EXCALIDRAW-LICENSE.txt');

// Retain license notices for dependencies distributed in the browser bundle.
const { readdir, readFile, writeFile } = await import('node:fs/promises');
const { join } = await import('node:path');
const notices = [];
async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
    const root = join(directory, entry.name);
    if (entry.name.startsWith('@')) { await collect(root); continue; }
    try {
      const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
      const files = (await readdir(root)).filter(name => /^(licen[sc]e|copying|notice)(\.|$)/i.test(name));
      const texts = [];
      for (const file of files) { try { texts.push(await readFile(join(root, file), 'utf8')); } catch {} }
      notices.push(`${pkg.name} ${pkg.version} (${pkg.license || 'see notice'})\n${texts.join('\n')}`);
      try { await collect(join(root, 'node_modules')); } catch {}
    } catch {}
  }
}
await collect('node_modules');
await writeFile('../polish/editor_static/THIRD-PARTY-NOTICES.txt', notices.sort().join('\n\n--------------------\n\n'));
