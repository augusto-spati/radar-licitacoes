import stylex from '@stylexjs/unplugin';
import react from '@vitejs/plugin-react';
import {defineConfig} from 'vitest/config';

// base './' + HashRouter: o build abre de qualquer pasta (S3, arquivo local).
// O plugin StyleX compila só o nosso código; o Astryx vem pré-compilado em astryx.css.
export default defineConfig({
  base: './',
  plugins: [stylex.vite(), react()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
