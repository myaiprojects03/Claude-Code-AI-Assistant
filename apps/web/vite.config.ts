import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 3000 matches the Keycloak client's redirect URIs (infra/keycloak/realm-assistant.json).
export default defineConfig({
  plugins: [react()],
  server: { port: 3000, strictPort: true },
  preview: { port: 3000, strictPort: true },
});
