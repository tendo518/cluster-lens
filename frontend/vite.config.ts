import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// Deliberately retain the backend's stable five-file asset contract. HTML is a
// server-rendered template, not a Vite HTML entry: placeholders must survive.
export default defineConfig({
  envDir: false,
  envPrefix: [],
  publicDir: "public",
  build: {
    target: "es2022",
    lib: {
      entry: fileURLToPath(new URL("./src/app.ts", import.meta.url)),
      formats: ["iife"],
      name: "Monitor",
      fileName: () => "app.js",
    },
    sourcemap: false,
  },
});
