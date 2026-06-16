import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend so the browser talks to a single
// origin (mirrors how the Node BFF serves things in production).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // SSE: keep the connection open and unbuffered
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("Accept-Encoding", "identity"));
        },
      },
    },
  },
});
