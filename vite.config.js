import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// Two entry points under one identity:
//   index.html  → desktop app  (src/main-desktop.jsx → StrataApp)
//   mobile.html → mobile app   (src/main-mobile.jsx  → MobileApp)
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        mobile: resolve(__dirname, "mobile.html"),
      },
    },
  },
});
