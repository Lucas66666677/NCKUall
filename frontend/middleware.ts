import { NextRequest, NextResponse } from "next/server";

import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";

const isProduction = process.env.NODE_ENV === "production";

function randomNonce() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
}

function originFromEnv(value: string | undefined) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

// CSP scheme-part matching does not let an `https:` source authorise a `wss:`
// URL, so an origin the browser both fetches from and opens a socket to has to
// be published in `connect-src` under both schemes. The hard-coded Render
// entries below already spell out both; anything configured has to as well.
function networkOrigins(origin: string | null) {
  if (!origin) {
    return [];
  }

  const socketOrigin = origin.replace(/^http(s)?:\/\//, "ws$1://");

  return socketOrigin === origin ? [origin] : [origin, socketOrigin];
}

function compactPolicy(directives: string[]) {
  return directives
    .map((directive) => directive.trim())
    .filter(Boolean)
    .join("; ");
}

function buildContentSecurityPolicy(nonce: string) {
  // Resolve the API origin the same way the browser bundle does, rather than
  // reading NEXT_PUBLIC_API_BASE_URL raw: a release that configures a private
  // or unparseable value falls back to the canonical origin, and that fallback
  // -- not the configured string -- is what the page actually connects to.
  const apiOrigin = originFromEnv(getPublicApiBaseUrl());
  const siteOrigin = originFromEnv(process.env.NEXT_PUBLIC_SITE_URL);
  const wsOrigin = originFromEnv(process.env.NEXT_PUBLIC_WS_URL);

  const campusSources = ["https://ncku.edu.tw", "https://*.ncku.edu.tw"];
  const vercelSources = ["https://vercel.app", "https://*.vercel.app"];
  const backendSources = [
    "https://nckuall.onrender.com",
    "https://nckuall-api.onrender.com",
    "wss://nckuall.onrender.com",
    "wss://nckuall-api.onrender.com",
  ];
  const supabaseSources = [
    "https://ebabyscelfctvvrokijy.supabase.co",
    "https://*.supabase.co",
    "wss://*.supabase.co",
  ];
  const localTestSources = [
    "http://localhost:3000",
    "http://127.0.0.1:10000",
  ];
  const googleSources = [
    "https://accounts.google.com",
    "https://oauth2.googleapis.com",
    "https://*.googleapis.com",
    "https://*.gstatic.com",
    "https://*.googleusercontent.com",
  ];
  const aiSources = [
    "https://api.openai.com",
    "https://generativelanguage.googleapis.com",
  ];
  const devSources = isProduction
    ? []
    : [
        "http://localhost:*",
        "http://127.0.0.1:*",
        "ws://localhost:*",
        "ws://127.0.0.1:*",
      ];
  const configuredSources = [
    // `getPublicWebSocketUrl` derives the notifications socket from the API
    // base URL, so the API origin needs its `wss:` form here too.
    ...networkOrigins(apiOrigin),
    siteOrigin,
    wsOrigin,
  ].filter((source): source is string => Boolean(source));
  const configuredScriptSources = [
    siteOrigin,
  ].filter((source): source is string => Boolean(source));
  const networkSources = [
    "'self'",
    ...configuredSources,
    ...campusSources,
    ...vercelSources,
    ...backendSources,
    ...supabaseSources,
    ...googleSources,
    ...aiSources,
    ...localTestSources,
    ...devSources,
  ];
  const scriptSources = [
    "'self'",
    `'nonce-${nonce}'`,
    "'strict-dynamic'",
    ...(isProduction ? [] : ["'unsafe-eval'", "'unsafe-inline'"]),
    ...configuredScriptSources,
    ...campusSources,
    ...vercelSources,
    ...backendSources,
    "https://ebabyscelfctvvrokijy.supabase.co",
    "https://*.supabase.co",
    "https://accounts.google.com",
    "https://*.googleapis.com",
    "https://*.gstatic.com",
    ...aiSources,
    ...devSources.filter((source) => source.startsWith("http")),
  ];

  return compactPolicy([
    "default-src 'self'",
    `script-src ${scriptSources.join(" ")}`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    `connect-src ${networkSources.join(" ")}`,
    [
      "img-src",
      "'self'",
      "data:",
      "blob:",
      ...campusSources,
      ...vercelSources,
      "https://ebabyscelfctvvrokijy.supabase.co",
      "https://*.supabase.co",
      ...googleSources,
    ].join(" "),
    "font-src 'self' data: https://fonts.gstatic.com",
    "media-src 'self' data: blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://accounts.google.com",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "manifest-src 'self'",
    "worker-src 'self' blob:",
    "upgrade-insecure-requests",
  ]);
}

export function middleware(request: NextRequest) {
  const nonce = randomNonce();
  const csp = buildContentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });

  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  response.headers.set("Cross-Origin-Resource-Policy", "same-origin");

  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.json|sw.js|workbox-|icons/|og/|.*\\.(?:png|jpg|jpeg|gif|webp|svg|ico|css|js|map|txt|xml|json)$).*)",
  ],
};
