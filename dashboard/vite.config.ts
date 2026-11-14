import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxyConfig = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
  "/ws": {
    target: "ws://127.0.0.1:8000",
    ws: true,
  },
};

const allowedHosts = true;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    allowedHosts,
    proxy: proxyConfig,
  },
  preview: {
    host: "127.0.0.1",
    port: 5173,
    allowedHosts,
    proxy: proxyConfig,
  },
});
