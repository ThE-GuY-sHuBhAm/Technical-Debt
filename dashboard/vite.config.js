// Vite build of the TDEA dashboard. `npm run build` writes tdea/api/dashboard, which is
// committed and shipped as package data, so a pip-only install needs no Node.js
// (CON-06); the Flask app serves it (tdea serve). `npm run dev` proxies /api to a
// running tdea serve.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: '../tdea/api/dashboard', emptyOutDir: true, sourcemap: false },
  server: { proxy: { '/api': 'http://127.0.0.1:5000' } },
});
