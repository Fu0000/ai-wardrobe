import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "@/services/api";
import {
  clearTelemetry,
  flushTelemetry,
  setTelemetryAccessTokenProvider,
  track,
  trackOnce,
  uploadProgressBucket,
  uploadSizeBucket,
} from "@/services/telemetry";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, apiRequest: vi.fn() };
});

const apiRequestMock = vi.mocked(apiRequest);
const storage = new Map<string, unknown>();
const QUEUE_KEY = "aiw:telemetry-queue:v1";

const assetId = "11111111-1111-4111-8111-111111111111";

function queuedEvents(): unknown[] {
  const value = storage.get(QUEUE_KEY);
  return Array.isArray(value) ? value : [];
}

describe("client telemetry delivery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    storage.clear();
    apiRequestMock.mockReset();
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    clearTelemetry();
  });

  afterEach(() => {
    clearTelemetry();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("persists a bounded event and removes it only after a complete receipt", async () => {
    track("asset.upload.started", {
      asset_id: assetId,
      size_bucket: "1_to_5mb",
    });
    await flushTelemetry();
    expect(queuedEvents()).toHaveLength(1);

    apiRequestMock.mockResolvedValue({
      accepted_count: 1,
      duplicate_count: 0,
    });
    setTelemetryAccessTokenProvider(() => "access-token");
    await flushTelemetry();

    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(apiRequestMock.mock.calls[0]?.[0]).toMatchObject({
      path: "/api/v1/client-events",
      method: "POST",
      accessToken: "access-token",
      body: {
        events: [
          {
            event_name: "asset.upload.started",
            event_version: 1,
            platform: "mp-weixin",
            app_channel: "wechat",
            properties: {
              asset_id: assetId,
              size_bucket: "1_to_5mb",
            },
          },
        ],
      },
    });
    expect(queuedEvents()).toHaveLength(0);
  });

  it("retries transient failures with backoff without duplicating the queue", async () => {
    track("asset.upload.started", {
      asset_id: assetId,
      size_bucket: "lt_1mb",
    });
    await flushTelemetry();
    apiRequestMock
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ accepted_count: 1, duplicate_count: 0 });
    setTelemetryAccessTokenProvider(() => "access-token");

    await flushTelemetry();
    expect(queuedEvents()).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(apiRequestMock).toHaveBeenCalledTimes(2);
    expect(queuedEvents()).toHaveLength(0);
  });

  it("keeps flush single-flight while a request is pending", async () => {
    track("asset.upload.started", {
      asset_id: assetId,
      size_bucket: "lt_1mb",
    });
    await flushTelemetry();
    let resolveRequest!: (value: {
      accepted_count: number;
      duplicate_count: number;
    }) => void;
    apiRequestMock.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    setTelemetryAccessTokenProvider(() => "access-token");

    const first = flushTelemetry();
    const second = flushTelemetry();
    expect(first).toBe(second);
    expect(apiRequestMock).toHaveBeenCalledTimes(1);

    resolveRequest({ accepted_count: 1, duplicate_count: 0 });
    await first;
    expect(queuedEvents()).toHaveLength(0);
  });

  it("isolates and drops one permanently invalid event without blocking valid ones", async () => {
    track("asset.upload.started", {
      asset_id: assetId,
      size_bucket: "lt_1mb",
    });
    track("asset.upload.started", {
      asset_id: "22222222-2222-4222-8222-222222222222",
      size_bucket: "1_to_5mb",
    });
    await flushTelemetry();
    apiRequestMock
      .mockRejectedValueOnce(
        new ApiError("CLIENT_EVENT_ENTITY_INVALID", "invalid", 422),
      )
      .mockResolvedValueOnce({ accepted_count: 1, duplicate_count: 0 })
      .mockRejectedValueOnce(
        new ApiError("CLIENT_EVENT_ENTITY_INVALID", "invalid", 422),
      );
    setTelemetryAccessTokenProvider(() => "access-token");

    await flushTelemetry();

    expect(apiRequestMock).toHaveBeenCalledTimes(3);
    expect(queuedEvents()).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("tracks first-view semantics once for the same business key", async () => {
    const first = trackOnce(
      `asset.upload.started:${assetId}`,
      "asset.upload.started",
      {
        asset_id: assetId,
        size_bucket: "lt_1mb",
      },
    );
    const second = trackOnce(
      `asset.upload.started:${assetId}`,
      "asset.upload.started",
      {
        asset_id: assetId,
        size_bucket: "lt_1mb",
      },
    );
    await flushTelemetry();

    expect(first).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(second).toBeNull();
    expect(queuedEvents()).toHaveLength(1);
  });
});

describe("telemetry buckets", () => {
  it("uses stable bounded upload buckets", () => {
    expect(uploadSizeBucket(100)).toBe("lt_1mb");
    expect(uploadSizeBucket(2 * 1024 * 1024)).toBe("1_to_5mb");
    expect(uploadSizeBucket(7 * 1024 * 1024)).toBe("5_to_10mb");
    expect(uploadSizeBucket(12 * 1024 * 1024)).toBe("10_to_20mb");
    expect(uploadProgressBucket(0)).toBe("0_to_24");
    expect(uploadProgressBucket(40)).toBe("25_to_49");
    expect(uploadProgressBucket(60)).toBe("50_to_74");
    expect(uploadProgressBucket(99)).toBe("75_to_99");
  });
});
