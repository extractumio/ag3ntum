import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.AG3NTUM_WEB_PORT ?? process.env.VITE_DEV_PORT ?? 50080),
    host: '0.0.0.0',
  },
});
