import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Use absolute paths so this config works when copied to /tmp/ at container startup
// (source tree is mounted read-only, Vite needs a writable dir for its temp config files)
const projectRoot = process.env.AG3NTUM_ROOT || '/';
const webClientDir = '/src/web_terminal_client';
const nodeModulesPath = `${webClientDir}/node_modules`;

export default defineConfig({
  plugins: [react()],
  cacheDir: `${nodeModulesPath}/.vite`,
  server: {
    fs: {
      allow: [projectRoot],
    },
  },
  resolve: {
    alias: {
      'react': `${nodeModulesPath}/react`,
      'react-dom': `${nodeModulesPath}/react-dom`,
      'react-router-dom': `${nodeModulesPath}/react-router-dom`,
      '@testing-library/jest-dom': `${nodeModulesPath}/@testing-library/jest-dom`,
      '@testing-library/react': `${nodeModulesPath}/@testing-library/react`,
      '@testing-library/user-event': `${nodeModulesPath}/@testing-library/user-event`,
      'msw': `${nodeModulesPath}/msw`,
      'vitest': `${nodeModulesPath}/vitest`,
      '@vitest/coverage-v8': `${nodeModulesPath}/@vitest/coverage-v8`,
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    root: projectRoot,
    setupFiles: ['tests/web_terminal_console/setup.ts'],
    include: ['tests/web_terminal_console/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/web_terminal_client/src/**/*.{ts,tsx}'],
      exclude: ['**/*.d.ts', '**/main.tsx'],
    },
    testTimeout: 10000,
  },
});
