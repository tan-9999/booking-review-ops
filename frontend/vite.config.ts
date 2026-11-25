import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to Django, so the browser sees one origin (no CORS setup needed).
// Backend on another port? Run:  API_TARGET=http://127.0.0.1:8001 npm run dev
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { "/api": target },
    },
  };
});
