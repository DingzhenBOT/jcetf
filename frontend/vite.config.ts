import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前端基址说明（见 DESIGN §0 / §7）：
//  - 生产由 Nginx 同源托管静态文件并反代 /api/* 到 FastAPI（无 FastAPI 鉴权层）。
//  - 开发用 Vite 代理把 /api 转发到本地 FastAPI（默认 127.0.0.1:8000）。
//  - API 基址通过 import.meta.env.VITE_API_BASE 覆盖，默认 '/api'（相对路径）。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          vue: ['vue', 'vue-router'],
        },
      },
    },
  },
})
