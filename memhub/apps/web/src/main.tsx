import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { installNativeDisplayStyles } from "./native-display-styles";
import { installRendererDebugLogCapture } from "./renderer-debug-log";
import { startUiLanguage } from "./ui-language";
import "./native-display-styles.css";
import "./r6-fonts.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing #root");
}

const stopUiLanguage = startUiLanguage();
const stopNativeDisplayStyles = installNativeDisplayStyles();
const stopRendererDebugLogs = installRendererDebugLogCapture();
import.meta.hot?.dispose(stopUiLanguage);
import.meta.hot?.dispose(stopNativeDisplayStyles);
import.meta.hot?.dispose(stopRendererDebugLogs);

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
