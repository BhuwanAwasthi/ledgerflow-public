import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: { port: 5173, strictPort: true },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
          query: ["@tanstack/react-query", "@tanstack/react-table"],
          editor: ["@uiw/react-codemirror", "@codemirror/lang-yaml", "@codemirror/lang-markdown", "@codemirror/lint"],
          flow: ["@xyflow/react"],
        },
      },
    },
  },
});
