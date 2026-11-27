import stylex from '@stylexjs/unplugin';
import react from '@vitejs/plugin-react';
import {defineConfig} from 'vitest/config';

// base './' + HashRouter: o build abre de qualquer pasta (S3, arquivo local).
// O plugin StyleX compila só o nosso código; o Astryx vem pré-compilado em astryx.css.
export default defineConfig({
  base: './',
  plugins: [stylex.vite(), react()],
  server: {proxy: {'/api': 'http://127.0.0.1:8000'}},
  build: {
    rolldownOptions: {
      output: {
        // Vendors em pedaços estáveis: um deploy só do nosso código não os invalida.
        manualChunks(id: string) {
          if (/node_modules\/(react|react-dom|react-router|scheduler)\//.test(id)) return 'vendor-react';
          if (id.includes('node_modules/@astryxdesign/')) return 'vendor-astryx';
          return undefined;
        },
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.mjs'],
  },
});
