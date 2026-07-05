import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/excalidraw/",
  plugins: [react()],
  build: {
    outDir: "../../sp/server/static/excalidraw",
    emptyOutDir: true,
  },
});
