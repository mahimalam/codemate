import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const developmentHeaders = { 'X-Vexp-Token': 'vexp-dev-session' };

export default defineConfig({
  plugins: [react()],
  root: import.meta.dirname,
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
    chunkSizeWarningLimit: 3000,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:7860', headers: developmentHeaders },
      '/ws': { target: 'ws://127.0.0.1:7860', ws: true, headers: developmentHeaders },
    },
  },
});
