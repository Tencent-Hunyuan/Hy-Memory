import { type GuiLocale, translate } from "@one-memory/contracts/gui-i18n";
import { APP_ORIGIN, STARTUP_SCREEN_PATH } from "./app-protocol";

export const STARTUP_SCREEN_URL = `${APP_ORIGIN}${STARTUP_SCREEN_PATH}`;

/** A window the moment it exists, instead of a window thirty seconds later.
 *
 * Startup installs the managed runtime and waits for Core, which on a cold
 * launch takes tens of seconds. That work cannot be made instant, but a blank
 * screen for all of it reads as an app that failed to open, so this page fills
 * the gap. It deliberately makes no IPC calls: it renders before `initialize`
 * has registered any handler, and anything it asked for would fail.
 */
export function startupScreenHtml(locale: GuiLocale = "zh-CN"): string {
  return `<!doctype html>
<html lang="${locale}">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'" />
    <title>MemHub</title>
    <style>
      :root {
        color-scheme: light dark;
      }
      html,
      body {
        height: 100%;
        margin: 0;
      }
      body {
        display: flex;
        align-items: center;
        justify-content: center;
        background: #e9edf2;
        color: #1b2027;
        font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
          "Helvetica Neue", Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
      }
      @media (prefers-color-scheme: dark) {
        body {
          background: #15181d;
          color: #e6e9ee;
        }
      }
      main {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 14px;
        user-select: none;
      }
      h1 {
        margin: 0;
        font-size: 17px;
        font-weight: 600;
        letter-spacing: 0.02em;
      }
      p {
        margin: 0;
        font-size: 13px;
        opacity: 0.62;
      }
      .bar {
        position: relative;
        width: 168px;
        height: 3px;
        margin-top: 4px;
        border-radius: 999px;
        overflow: hidden;
        background: rgba(127, 138, 153, 0.22);
      }
      .bar span {
        position: absolute;
        inset: 0 auto 0 0;
        width: 38%;
        border-radius: 999px;
        background: #3b6ef6;
        animation: sweep 1.25s ease-in-out infinite;
      }
      @media (prefers-color-scheme: dark) {
        .bar span {
          background: #6b93ff;
        }
      }
      @keyframes sweep {
        0% {
          transform: translateX(-100%);
        }
        100% {
          transform: translateX(263%);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .bar span {
          animation: none;
          width: 100%;
          opacity: 0.5;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <h1>MemHub</h1>
      <p>${translate(locale, "startupPreparing")}</p>
      <div class="bar"><span></span></div>
    </main>
  </body>
</html>
`;
}
