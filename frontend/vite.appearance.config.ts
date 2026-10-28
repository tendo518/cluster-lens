import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// A separate classic bundle is essential: module scripts defer execution and
// would flash the wrong theme before styles/first paint.
export default defineConfig({
  envDir: false,
  envPrefix: [],
  publicDir: false,
  build: {
    target: "es2022",
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL("./src/appearance.ts", import.meta.url)),
      formats: ["iife"],
      name: "MonitorAppearance",
      fileName: () => "appearance.js",
    },
    sourcemap: false,
  },
});
