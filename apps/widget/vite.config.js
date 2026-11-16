import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

// `npm run build` -> dist/assistant-chat.js: one self-contained file (JS + CSS) for any site:
//   <script src="https://your-cdn/assistant-chat.js" data-api="https://api.example.com"></script>
export default defineConfig({
  plugins: [preact()],
  root: ".",
  server: { port: 5173, open: "/demo/index.html" },
  build: {
    lib: {
      entry: "src/main.jsx",
      name: "AssistantChat",
      formats: ["iife"],
      fileName: () => "assistant-chat.js",
    },
    cssCodeSplit: false,
    minify: true,
  },
});
