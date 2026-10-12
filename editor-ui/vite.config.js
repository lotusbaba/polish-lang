import { defineConfig } from 'vite';
export default defineConfig({ build: {outDir: '../polish/editor_static', emptyOutDir: true}, define: {'process.env.IS_PREACT': 'false'} });
