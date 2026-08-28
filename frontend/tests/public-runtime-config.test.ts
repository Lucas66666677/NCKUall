import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getPublicApiBaseUrl,
  getPublicWebSocketUrl,
} from "@/lib/public-runtime-config";

const CANONICAL_API_ORIGIN = "https://nckuall.onrender.com";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("getPublicApiBaseUrl", () => {
  it("uses the configured origin and drops trailing slashes", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com/");

    expect(getPublicApiBaseUrl()).toBe("https://api.example.com");
  });

  it("rewrites the legacy Render host to the canonical origin", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://nckuall-api.onrender.com");

    expect(getPublicApiBaseUrl()).toBe(CANONICAL_API_ORIGIN);
  });

  it("drops a trailing /api path segment", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com/api");

    expect(getPublicApiBaseUrl()).toBe("https://api.example.com");
  });

  it("falls back to the canonical origin in production builds", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", undefined);

    expect(getPublicApiBaseUrl()).toBe(CANONICAL_API_ORIGIN);
  });

  it("treats a blank production value as unset rather than as an origin", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "   ");

    expect(getPublicApiBaseUrl()).toBe(CANONICAL_API_ORIGIN);
  });

  it("never points a production bundle at loopback", () => {
    vi.stubEnv("NODE_ENV", "production");

    for (const value of [undefined, "", "  "]) {
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", value);

      const baseUrl = getPublicApiBaseUrl();
      expect(baseUrl).not.toContain("127.0.0.1");
      expect(baseUrl).not.toContain("localhost");
      expect(new URL(baseUrl).protocol).toBe("https:");
    }
  });

  it("still defaults to the local backend outside production", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", undefined);

    expect(getPublicApiBaseUrl()).toBe("http://127.0.0.1:8000");
  });
});

describe("getPublicWebSocketUrl", () => {
  it("derives a secure socket URL from the production fallback", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", undefined);

    expect(getPublicWebSocketUrl().toString()).toBe(
      "wss://nckuall.onrender.com/ws/notifications",
    );
  });
});

// A production build that has NEXT_PUBLIC_API_BASE_URL set is the case the
// unset/blank fallback above cannot help with: the value is inlined verbatim,
// so a developer origin copied into the release environment ships silently and
// points every anonymous visitor's browser at their own machine or LAN.
describe("getPublicApiBaseUrl in a production release", () => {
  const PRIVATE_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://127.1.2.3:8000",
    "http://localhost:8000",
    "http://api.localhost:8000",
    "https://LOCALHOST:8000",
    "http://[::1]:8000",
    "http://[fc00::1]:8000",
    "http://[fd12:3456:789a::1]:8000",
    "http://[fe80::1]:8000",
    "http://[::ffff:127.0.0.1]:8000",
    "http://0.0.0.0:8000",
    "http://10.0.0.5:8000",
    "http://172.16.4.9:8000",
    "http://172.31.255.1:8000",
    "http://192.168.1.20:8000",
    "http://169.254.10.10:8000",
  ];

  it.each(PRIVATE_ORIGINS)(
    "refuses to publish %s as the public API origin",
    (origin) => {
      vi.stubEnv("NODE_ENV", "production");
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", origin);

      expect(getPublicApiBaseUrl()).toBe(CANONICAL_API_ORIGIN);
    },
  );

  it.each(PRIVATE_ORIGINS)(
    "refuses to derive a notifications socket from %s",
    (origin) => {
      vi.stubEnv("NODE_ENV", "production");
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", origin);

      expect(getPublicWebSocketUrl().toString()).toBe(
        "wss://nckuall.onrender.com/ws/notifications",
      );
    },
  );

  it("falls back when the configured value is not a usable absolute URL", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "nckuall.onrender.com");

    expect(getPublicApiBaseUrl()).toBe(CANONICAL_API_ORIGIN);
  });

  it("leaves a legitimately configured public origin alone", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.nckuall.com");

    expect(getPublicApiBaseUrl()).toBe("https://api.nckuall.com");
  });

  it("does not mistake public hosts for private ones", () => {
    vi.stubEnv("NODE_ENV", "production");

    for (const origin of [
      "https://172.32.0.1", // just outside the RFC 1918 172.16/12 block
      "https://11.0.0.1", // adjacent to 10/8
      "https://192.169.0.1", // adjacent to 192.168/16
      "https://not-localhost.example.com",
      "https://localhost.example.com",
    ]) {
      vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", origin);

      expect(getPublicApiBaseUrl()).toBe(origin);
    }
  });

  it("still lets local development point at the local backend", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000");

    expect(getPublicApiBaseUrl()).toBe("http://127.0.0.1:8000");
  });
});
