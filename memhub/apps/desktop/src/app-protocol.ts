import path from "node:path";
import { pathToFileURL } from "node:url";

import { net, protocol } from "electron";

export const APP_ORIGIN = "one-memory://app";

/** The route the startup placeholder is served from. It is never a file: the
 *  placeholder has to load before anything in the web bundle is trusted. */
export const STARTUP_SCREEN_PATH = "/__startup";

export function isStartupScreenRequest(requestUrl: string): boolean {
  const url = new URL(requestUrl);
  return (
    url.protocol === "one-memory:" &&
    url.host === "app" &&
    url.pathname === STARTUP_SCREEN_PATH
  );
}

export function resolveWebResource(
  webRoot: string,
  requestUrl: string,
): string | null {
  const url = new URL(requestUrl);
  if (url.protocol !== "one-memory:" || url.host !== "app") return null;
  // Reserved for the in-memory startup placeholder: a file of that name in the
  // web bundle is not the placeholder and must not be reachable as one.
  if (isStartupScreenRequest(requestUrl)) return null;
  const pathname = decodeURIComponent(url.pathname);
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const resolvedRoot = path.resolve(webRoot);
  const resolved = path.resolve(resolvedRoot, requested);
  const relative = path.relative(resolvedRoot, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    return null;
  }
  return resolved;
}

export function installAppProtocol(
  webRoot: string,
  options: { startupScreen?: () => string } = {},
): void {
  protocol.handle("one-memory", (request) => {
    if (options.startupScreen && isStartupScreenRequest(request.url)) {
      return new Response(options.startupScreen(), {
        status: 200,
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    const resource = resolveWebResource(webRoot, request.url);
    if (!resource) {
      return new Response("Not found", { status: 404 });
    }
    return net.fetch(pathToFileURL(resource).toString());
  });
}
