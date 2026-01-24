/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, '../..');
const nodeModulesPath = path.resolve(__dirname, 'node_modules');

export default defineConfig({
  plugins: [react()],
  cacheDir: path.resolve(__dirname, 'node_modules/.vite'),
  server: {
    fs: {
      allow: [projectRoot],
    },
  },
  resolve: {
    alias: {
      'react': path.resolve(nodeModulesPath, 'react'),
      'react-dom': path.resolve(nodeModulesPath, 'react-dom'),
      'react-router-dom': path.resolve(nodeModulesPath, 'react-router-dom'),
      '@testing-library/jest-dom': path.resolve(nodeModulesPath, '@testing-library/jest-dom'),
      '@testing-library/react': path.resolve(nodeModulesPath, '@testing-library/react'),
      '@testing-library/user-event': path.resolve(nodeModulesPath, '@testing-library/user-event'),
      'msw': path.resolve(nodeModulesPath, 'msw'),
      'vitest': path.resolve(nodeModulesPath, 'vitest'),
      '@vitest/coverage-v8': path.resolve(nodeModulesPath, '@vitest/coverage-v8'),
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
