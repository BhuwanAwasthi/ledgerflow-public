import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ command, mode }) => {
  // The AUTH_MODE=dev stub login ships only with the dev server, or a build that asks for it
  // (VITE_DEV_LOGIN=true, e2e against a dev-mode API). Every other build resolves "@/lib/devLogin" to the
  // empty module; scripts/check-bundle.mjs fails the build if any of it slipped into dist/.
  const devLogin = command === "serve" || loadEnv(mode, process.cwd(), "VITE_").VITE_DEV_LOGIN === "true";
  return {
    plugins: [react()],
    resolve: {
      alias: [
        { find: /^@\/lib\/devLogin$/, replacement: path.resolve(__dirname, devLogin ? "src/lib/devLogin.tsx" : "src/lib/devLogin.off.ts") },
        { find: "@", replacement: path.resolve(__dirname, "src") },
      ],
    },
    server: { port: 5173, strictPort: true },
    preview: { port: 4173, strictPort: true },
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
  };
});
