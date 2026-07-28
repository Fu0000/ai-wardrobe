import { ApiError, apiRequest } from "@/services/api";

const QUEUE_STORAGE_KEY = "aiw:telemetry-queue:v1";
const SEEN_STORAGE_KEY = "aiw:telemetry-seen:v1";
const MAX_QUEUE_SIZE = 100;
const MAX_SEEN_SIZE = 500;
const MAX_BATCH_SIZE = 20;
const SEEN_RETENTION_MS = 30 * 24 * 60 * 60 * 1_000;

type ClientEventProperties = {
  "asset.upload.started": {
    asset_id: string;
    size_bucket: "lt_1mb" | "1_to_5mb" | "5_to_10mb" | "10_to_20mb";
  };
  "asset.upload.interrupted": {
    asset_id: string;
    reason: "network" | "timeout" | "cancelled" | "unknown";
    progress_bucket: "0_to_24" | "25_to_49" | "50_to_74" | "75_to_99";
  };
  "diagnosis.result.viewed": {
    diagnosis_id: string;
    score_bucket: "unknown" | "0_to_59" | "60_to_79" | "80_to_100";
  };
  "diagnosis.optimization.clicked": {
    diagnosis_id: string;
  };
  "optimization.before_after.viewed": {
    optimization_id: string;
  };
};

export type ClientEventName = keyof ClientEventProperties;

type QueuedClientEvent = {
  [Name in ClientEventName]: {
    event_id: string;
    event_name: Name;
    event_version: 1;
    occurred_at: string;
    session_id: string;
    client_version: string;
    platform: "mp-weixin";
    app_channel: "wechat";
    properties: ClientEventProperties[Name];
  };
}[ClientEventName];

interface ClientEventReceipt {
  accepted_count: number;
  duplicate_count: number;
}

interface SeenEntry {
  key: string;
  seenAt: number;
}

type AccessTokenProvider = () => string | null;

let accessTokenProvider: AccessTokenProvider | null = null;
let flushPromise: Promise<void> | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let retryAttempt = 0;
const sessionId = uuidV4().replaceAll("-", "");

function uuidV4(): string {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = ((bytes[6] ?? 0) % 16) + 64;
  bytes[8] = ((bytes[8] ?? 0) % 64) + 128;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

function loadQueue(): QueuedClientEvent[] {
  const stored = uni.getStorageSync(QUEUE_STORAGE_KEY) as
    | QueuedClientEvent[]
    | "";
  return Array.isArray(stored) ? stored.slice(-MAX_QUEUE_SIZE) : [];
}

function saveQueue(events: QueuedClientEvent[]): void {
  if (events.length === 0) {
    uni.removeStorageSync(QUEUE_STORAGE_KEY);
    return;
  }
  uni.setStorageSync(QUEUE_STORAGE_KEY, events.slice(-MAX_QUEUE_SIZE));
}

function loadSeen(): SeenEntry[] {
  const stored = uni.getStorageSync(SEEN_STORAGE_KEY) as SeenEntry[] | "";
  if (!Array.isArray(stored)) {
    return [];
  }
  const cutoff = Date.now() - SEEN_RETENTION_MS;
  return stored
    .filter(
      (entry) =>
        typeof entry?.key === "string" &&
        typeof entry.seenAt === "number" &&
        entry.seenAt >= cutoff,
    )
    .slice(-MAX_SEEN_SIZE);
}

function removeEvents(eventIds: Set<string>): void {
  saveQueue(loadQueue().filter((event) => !eventIds.has(event.event_id)));
}

function isPermanentClientError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.statusCode >= 400 &&
    error.statusCode < 500 &&
    ![401, 403, 408, 429].includes(error.statusCode)
  );
}

function scheduleRetry(): void {
  if (retryTimer) {
    return;
  }
  const delay = Math.min(60_000, 1_000 * 2 ** retryAttempt);
  retryAttempt += 1;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    void flushTelemetry();
  }, delay);
}

async function deliverBatch(
  batch: QueuedClientEvent[],
  accessToken: string,
): Promise<void> {
  try {
    const receipt = await apiRequest<
      ClientEventReceipt,
      { events: QueuedClientEvent[] }
    >({
      path: "/api/v1/client-events",
      method: "POST",
      body: { events: batch },
      accessToken,
      timeoutMs: 10_000,
    });
    if (
      receipt.accepted_count + receipt.duplicate_count !==
      batch.length
    ) {
      throw new Error("client event receipt count mismatch");
    }
    removeEvents(new Set(batch.map((event) => event.event_id)));
  } catch (error) {
    if (!isPermanentClientError(error)) {
      throw error;
    }
    if (batch.length > 1) {
      const middle = Math.ceil(batch.length / 2);
      await deliverBatch(batch.slice(0, middle), accessToken);
      await deliverBatch(batch.slice(middle), accessToken);
      return;
    }
    // 单条永久无效事件不能阻塞后续整个队列；服务端仍是最终 Schema/归属门禁。
    removeEvents(new Set([batch[0]?.event_id ?? ""]));
  }
}

async function performFlush(): Promise<void> {
  while (true) {
    const accessToken = accessTokenProvider?.();
    const queue = loadQueue();
    if (!accessToken || queue.length === 0) {
      return;
    }
    try {
      await deliverBatch(queue.slice(0, MAX_BATCH_SIZE), accessToken);
      retryAttempt = 0;
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    } catch {
      scheduleRetry();
      return;
    }
  }
}

export function setTelemetryAccessTokenProvider(
  provider: AccessTokenProvider | null,
): void {
  accessTokenProvider = provider;
}

export function track<Name extends ClientEventName>(
  eventName: Name,
  properties: ClientEventProperties[Name],
): string {
  const eventId = uuidV4();
  const event = {
    event_id: eventId,
    event_name: eventName,
    event_version: 1,
    occurred_at: new Date().toISOString(),
    session_id: sessionId,
    client_version: import.meta.env.VITE_APP_VERSION ?? "0.1.0",
    platform: "mp-weixin",
    app_channel: "wechat",
    properties,
  } as QueuedClientEvent;
  saveQueue([...loadQueue(), event]);
  void flushTelemetry();
  return eventId;
}

export function trackOnce<Name extends ClientEventName>(
  key: string,
  eventName: Name,
  properties: ClientEventProperties[Name],
): string | null {
  const seen = loadSeen();
  if (seen.some((entry) => entry.key === key)) {
    return null;
  }
  uni.setStorageSync(
    SEEN_STORAGE_KEY,
    [...seen, { key, seenAt: Date.now() }].slice(-MAX_SEEN_SIZE),
  );
  return track(eventName, properties);
}

export function flushTelemetry(): Promise<void> {
  if (!flushPromise) {
    flushPromise = performFlush().finally(() => {
      flushPromise = null;
    });
  }
  return flushPromise;
}

export function clearTelemetry(): void {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  retryAttempt = 0;
  flushPromise = null;
  accessTokenProvider = null;
  uni.removeStorageSync(QUEUE_STORAGE_KEY);
  uni.removeStorageSync(SEEN_STORAGE_KEY);
}

export function uploadSizeBucket(
  sizeBytes: number,
): ClientEventProperties["asset.upload.started"]["size_bucket"] {
  if (sizeBytes < 1024 * 1024) {
    return "lt_1mb";
  }
  if (sizeBytes < 5 * 1024 * 1024) {
    return "1_to_5mb";
  }
  if (sizeBytes < 10 * 1024 * 1024) {
    return "5_to_10mb";
  }
  return "10_to_20mb";
}

export function uploadProgressBucket(
  progress: number,
): ClientEventProperties["asset.upload.interrupted"]["progress_bucket"] {
  if (progress < 25) {
    return "0_to_24";
  }
  if (progress < 50) {
    return "25_to_49";
  }
  if (progress < 75) {
    return "50_to_74";
  }
  return "75_to_99";
}
