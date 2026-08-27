const CANONICAL_RENDER_API_ORIGIN = "https://nckuall.onrender.com";
const LOCAL_DEV_API_ORIGIN = "http://127.0.0.1:8000";
const LEGACY_RENDER_ORIGINS = new Set([
  "https://nckuall-api.onrender.com",
  "wss://nckuall-api.onrender.com",
]);

function normalizeKnownBackendOrigin(value: string): string {
  try {
    const url = new URL(value);
    if (LEGACY_RENDER_ORIGINS.has(url.origin)) {
      const canonical = new URL(CANONICAL_RENDER_API_ORIGIN);
      url.protocol = canonical.protocol;
      url.host = canonical.host;
    }

    if (url.pathname === "/api" || url.pathname === "/api/") {
      url.pathname = "";
    }

    return url.toString().replace(/\/+$/, "");
  } catch {
    return value;
  }

  return value.replace(/\/$/, "");
}

function resolveConfiguredApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configured) {
    return configured;
  }

  // NEXT_PUBLIC_* values are inlined at build time, so a production bundle
  // built without NEXT_PUBLIC_API_BASE_URL would send every anonymous
  // visitor's browser to their own loopback address. Fall back to the same
  // canonical origin the server modules already default to; the loopback
  // default is only ever useful during local development.
  return process.env.NODE_ENV === "production"
    ? CANONICAL_RENDER_API_ORIGIN
    : LOCAL_DEV_API_ORIGIN;
}

export function getPublicApiBaseUrl(): string {
  return normalizeKnownBackendOrigin(resolveConfiguredApiBaseUrl());
}

export function getPublicWebSocketUrl(pathname = "/ws/notifications"): URL {
  const apiBaseUrl = getPublicApiBaseUrl();
  const configuredWebSocketUrl = process.env.NEXT_PUBLIC_WS_URL;
  const source = apiBaseUrl || configuredWebSocketUrl || window.location.origin;
  const url = new URL(
    normalizeKnownBackendOrigin(source),
    window.location.origin,
  );

  url.pathname = pathname;
  url.search = "";

  if (url.protocol === "http:") {
    url.protocol = "ws:";
  } else if (url.protocol === "https:") {
    url.protocol = "wss:";
  }

  return url;
}
