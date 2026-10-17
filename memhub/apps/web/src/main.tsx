import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { LanguageProvider } from "./gui-language";
import { installNativeDisplayStyles } from "./native-display-styles";
import { installRendererDebugLogCapture } from "./renderer-debug-log";
import "./native-display-styles.css";
import "./r6-fonts.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing #root");
}

const stopNativeDisplayStyles = installNativeDisplayStyles();
const stopRendererDebugLogs = installRendererDebugLogCapture();
import.meta.hot?.dispose(stopNativeDisplayStyles);
import.meta.hot?.dispose(stopRendererDebugLogs);

createRoot(root).render(
  <StrictMode>
    <LanguageProvider>
      <App />
    </LanguageProvider>
  </StrictMode>,
);
