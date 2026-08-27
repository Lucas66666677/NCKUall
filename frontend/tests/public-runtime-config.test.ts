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
