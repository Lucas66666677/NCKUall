const CANONICAL_RENDER_API_ORIGIN = "https://nckuall-api.onrender.com";
const LEGACY_RENDER_ORIGINS = new Set([
  "https://nckuall.onrender.com",
  "wss://nckuall.onrender.com",
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

export function getPublicApiBaseUrl(): string {
  return normalizeKnownBackendOrigin(
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
  );
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
