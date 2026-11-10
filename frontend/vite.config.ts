import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发时把接口请求转给本机的接口进程；部署时由 nginx 转发
const API_TARGET = process.env.KB_API_TARGET || "http://127.0.0.1:8210";

export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.KB_WEB_HOST || "127.0.0.1",
    port: 8220,
    proxy: {
      "/api": API_TARGET,
      "/open": API_TARGET,
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
