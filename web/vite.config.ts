/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served at /app by the API (same origin, ADR-028). `npm run dev` proxies /v1 to a local API.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: true, target: "es2022", chunkSizeWarningLimit: 400 },
  server: { port: 5173, proxy: { "/v1": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test/setup.ts"] },
});
