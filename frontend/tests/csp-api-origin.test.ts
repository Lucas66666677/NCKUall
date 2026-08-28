import { afterEach, describe, expect, it, vi } from "vitest";

// `middleware.ts` reads NODE_ENV once at module scope, so every case here has
// to stub the environment and then re-import both modules. Loading them through
// a helper keeps the production branch of the policy under test instead of the
// looser development one, which allows loopback sources that would mask a
// missing origin.
async function connectSrcFor(env: Record<string, string | undefined>) {
  vi.resetModules();
  vi.stubEnv("NODE_ENV", "production");
  for (const [name, value] of Object.entries(env)) {
    vi.stubEnv(name, value);
  }

  const { NextRequest } = await import("next/server");
  const { middleware } = await import("@/middleware");
  const { getPublicApiBaseUrl, getPublicWebSocketUrl } = await import(
    "@/lib/public-runtime-config"
  );

  const response = middleware(new NextRequest("https://nckuall.example/"));
  const policy = response.headers.get("Content-Security-Policy") ?? "";
  const directive = policy
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("connect-src "));

  expect(directive, "the policy always declares connect-src").toBeTruthy();

  return {
    sources: directive!.slice("connect-src ".length).split(/\s+/),
    apiBaseUrl: getPublicApiBaseUrl(),
    webSocketUrl: getPublicWebSocketUrl().toString(),
  };
}

// A trimmed-down version of the CSP source matching browsers apply. The part
// that matters here is the scheme rule: an `https:` source expression matches
// `https:` URLs only, so it does not authorise a `wss:` connection. That is why
// the hard-coded Render entries in the policy are spelled out under both
// schemes, and it is the rule a configured origin has to satisfy too.
function schemeMatches(source: string, target: string): boolean {
  if (source === target) {
    return true;
  }

  return (
    (source === "http:" && target === "https:") ||
    (source === "ws:" && ["wss:", "http:", "https:"].includes(target)) ||
    (source === "wss:" && target === "https:")
  );
}

function permits(sources: string[], target: string): boolean {
  const url = new URL(target);

  return sources.some((source) => {
    if (source.startsWith("'")) {
      return false;
    }

    let parsed: URL;
    try {
      parsed = new URL(source.replace("://*.", "://wildcard."));
    } catch {
      return false;
    }

    if (!schemeMatches(parsed.protocol, url.protocol)) {
      return false;
    }

    const hostMatches = source.includes("://*.")
      ? url.hostname.endsWith(parsed.hostname.replace(/^wildcard\./, "."))
      : parsed.hostname === url.hostname;
    const portMatches =
      parsed.port === "*" ||
      source.endsWith(":*") ||
      parsed.port === url.port ||
      (parsed.port === "" && url.port === "");

    return hostMatches && portMatches;
  });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

// The frontend serves its own CSP, and `getPublicWebSocketUrl` derives the
// notifications socket from whatever API origin the bundle resolved. Nothing
// ties the two together: the release that moves the backend off the hard-coded
// Render host -- the documented production shape, `https://api.example.com` --
// builds, lints, passes every unit and end-to-end test, and then has the
// browser refuse the socket the page opens, because `connect-src` published
// only the `https:` form of that origin.
describe("the served CSP admits the API origin the bundle resolved", () => {
  it("permits both the fetch and socket origins of a custom API domain", async () => {
    const { sources, apiBaseUrl, webSocketUrl } = await connectSrcFor({
      NEXT_PUBLIC_API_BASE_URL: "https://api.nckuall.example",
      NEXT_PUBLIC_WS_URL: undefined,
    });

    expect(apiBaseUrl).toBe("https://api.nckuall.example");
    expect(webSocketUrl).toBe(
      "wss://api.nckuall.example/ws/notifications",
    );
    expect(permits(sources, `${apiBaseUrl}/api/courses`)).toBe(true);
    expect(permits(sources, webSocketUrl)).toBe(true);
  });

  it("permits the socket origin even when NEXT_PUBLIC_WS_URL names a stale host", async () => {
    // The socket URL is derived from the API base URL, so a WS variable left
    // pointing at the previous backend widens the policy in the wrong place.
    const { sources, webSocketUrl } = await connectSrcFor({
      NEXT_PUBLIC_API_BASE_URL: "https://api.nckuall.example",
      NEXT_PUBLIC_WS_URL: "wss://old-backend.example/ws/notifications",
    });

    expect(webSocketUrl).toBe(
      "wss://api.nckuall.example/ws/notifications",
    );
    expect(permits(sources, webSocketUrl)).toBe(true);
  });

  it("still permits the canonical origin when the API base URL is unset", async () => {
    const { sources, apiBaseUrl, webSocketUrl } = await connectSrcFor({
      NEXT_PUBLIC_API_BASE_URL: undefined,
      NEXT_PUBLIC_WS_URL: undefined,
    });

    expect(apiBaseUrl).toBe("https://nckuall.onrender.com");
    expect(permits(sources, `${apiBaseUrl}/api/courses`)).toBe(true);
    expect(permits(sources, webSocketUrl)).toBe(true);
  });

  it("permits the fallback origin a rejected private value resolves to", async () => {
    // The loopback guard rewrites a private origin to the canonical one, so the
    // policy has to follow the resolved value, not the configured string.
    const { sources, apiBaseUrl, webSocketUrl } = await connectSrcFor({
      NEXT_PUBLIC_API_BASE_URL: "http://192.168.1.20:8000",
      NEXT_PUBLIC_WS_URL: undefined,
    });

    expect(apiBaseUrl).toBe("https://nckuall.onrender.com");
    expect(permits(sources, `${apiBaseUrl}/api/courses`)).toBe(true);
    expect(permits(sources, webSocketUrl)).toBe(true);
    expect(permits(sources, "http://192.168.1.20:8000/api/courses")).toBe(false);
  });
});
