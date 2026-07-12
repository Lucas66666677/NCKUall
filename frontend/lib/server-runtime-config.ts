import "server-only";

const CANONICAL_RENDER_API_ORIGIN = "https://nckuall.onrender.com";
const LEGACY_RENDER_ORIGINS = new Set([
  "https://nckuall-api.onrender.com",
  "wss://nckuall-api.onrender.com",
]);

function normalizeBackendOrigin(value: string): string {
  try {
    const url = new URL(value);
    const vercelUrl = process.env.VERCEL_URL
      ? `https://${process.env.VERCEL_URL}`
      : null;

    if (
      LEGACY_RENDER_ORIGINS.has(url.origin) ||
      (vercelUrl && url.origin === new URL(vercelUrl).origin)
    ) {
      const canonical = new URL(CANONICAL_RENDER_API_ORIGIN);
      url.protocol = canonical.protocol;
      url.host = canonical.host;
      url.pathname = "";
      url.search = "";
      url.hash = "";
    }

    if (url.pathname === "/api" || url.pathname === "/api/") {
      url.pathname = "";
    }

    return url.toString().replace(/\/+$/, "");
  } catch {
    return value.replace(/\/+$/, "");
  }
}

export function getServerApiBaseUrl(): string {
  return normalizeBackendOrigin(
    process.env.API_BASE_URL ??
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      CANONICAL_RENDER_API_ORIGIN,
  );
}
