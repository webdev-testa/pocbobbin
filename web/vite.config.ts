import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    target: "es2020",
  },
  // `npm run dev` against a running `behavior-review ui --no-browser` (default port 8765); open the
  // dev page with that server's ?token=. changeOrigin keeps the Host the server accepts.
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true } },
  },
});
