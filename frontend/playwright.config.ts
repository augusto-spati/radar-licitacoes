import {defineConfig, devices} from '@playwright/test';

// Chromium apenas, em dois tamanhos: celular (390x844) e desktop (1280x800).
// O servidor é o build de produção com a fonte de exemplo, via `vite preview`.
export default defineConfig({
  testDir: 'e2e',
  // Referências só do Linux (container do CI): sem sufixo de plataforma no nome.
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{testFilePath}/{arg}{ext}',
  forbidOnly: !!process.env.CI,
  // Sem nova tentativa: um screenshot que passa na segunda vez esconderia a diferença.
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', {open: 'never'}]] : 'list',
  use: {baseURL: 'http://localhost:4173', trace: 'retain-on-failure'},
  webServer: {
    command: 'npm run build && npx vite preview --port 4173 --strictPort',
    url: 'http://localhost:4173',
    env: {VITE_FONTE: 'exemplo'},
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [
    {name: 'celular', use: {...devices['Desktop Chrome'], viewport: {width: 390, height: 844}}},
    {name: 'desktop', use: {...devices['Desktop Chrome'], viewport: {width: 1280, height: 800}}},
  ],
});
