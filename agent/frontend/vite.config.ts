import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 透传到后端，避免 CORS 和硬编码端口
      '/api': { target: 'http://127.0.0.1:8848', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
