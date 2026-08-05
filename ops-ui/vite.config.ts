import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  server: {
    proxy: {
      "/ws/hermes": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    target: "es2020",
    cssCodeSplit: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/react-dom")) return "react-vendor";
          if (id.includes("node_modules/react/")) return "react-vendor";
          if (id.includes("node_modules/scheduler")) return "react-vendor";
          if (id.includes("node_modules/xstate")) return "xstate-vendor";
          if (id.includes("node_modules/@xstate/react")) return "xstate-vendor";
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
  },
});
