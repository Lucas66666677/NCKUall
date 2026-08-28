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

// Hostnames that only ever resolve inside the machine or network doing the
// resolving. A browser asked to reach one of these from a public page talks to
// the visitor's own computer or LAN, not to our backend.
function isUnreachableFromThePublicInternet(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");

  if (host === "localhost" || host.endsWith(".localhost")) {
    return true;
  }

  // IPv6 loopback and the unspecified address, in their common spellings.
  if (host === "::1" || host === "::" || host === "0:0:0:0:0:0:0:1") {
    return true;
  }

  const ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!ipv4) {
    return false;
  }

  const [a, b] = ipv4.slice(1).map(Number);

  return (
    a === 127 || // loopback
    a === 0 || // "this host on this network"
    a === 10 || // RFC 1918
    (a === 172 && b >= 16 && b <= 31) || // RFC 1918
    (a === 192 && b === 168) || // RFC 1918
    (a === 169 && b === 254) // link-local
  );
}

function resolveConfiguredApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  // NEXT_PUBLIC_* values are inlined at build time, so a production bundle
  // built without NEXT_PUBLIC_API_BASE_URL -- or with a developer's loopback
  // value copied into the release environment -- would send every anonymous
  // visitor's browser to their own machine. Neither mistake is visible in the
  // built output, so reject both here and fall back to the same canonical
  // origin the server modules already default to. Outside production the
  // local backend is exactly what a developer wants.
  if (process.env.NODE_ENV !== "production") {
    return configured || LOCAL_DEV_API_ORIGIN;
  }

  if (!configured) {
    return CANONICAL_RENDER_API_ORIGIN;
  }

  try {
    if (isUnreachableFromThePublicInternet(new URL(configured).hostname)) {
      return CANONICAL_RENDER_API_ORIGIN;
    }
  } catch {
    // Not a parseable absolute URL, so it cannot be a usable API origin for a
    // browser either. normalizeKnownBackendOrigin would pass it through
    // verbatim; prefer the origin we know serves the public site.
    return CANONICAL_RENDER_API_ORIGIN;
  }

  return configured;
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
