import { render } from "preact";
import { Widget } from "./Widget.jsx";
import css from "./styles.css?inline";

// Configuration comes from the embedding <script> tag's data-* attributes.
const script =
  document.currentScript ||
  document.querySelector("script[data-api][src*='assistant-chat'], script[data-api]");

const config = {
  api: (script?.dataset.api || "http://localhost:8000").replace(/\/$/, ""),
  title: script?.dataset.title || "Acme IT Consulting",
  accent: script?.dataset.accent || "",
  privacyUrl: script?.dataset.privacyUrl || "#privacy",
};

function mount() {
  if (document.getElementById("assistant-chat-host")) return; // embedded twice
  const host = document.createElement("div");
  host.id = "assistant-chat-host";
  document.body.appendChild(host);
  // Shadow DOM: the host page's CSS can't break the widget, and ours can't leak out.
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  root.appendChild(style);
  if (config.accent) host.style.setProperty("--ac-accent", config.accent);
  const app = document.createElement("div");
  root.appendChild(app);
  render(<Widget config={config} />, app);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
