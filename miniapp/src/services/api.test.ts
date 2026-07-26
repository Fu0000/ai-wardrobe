import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, setReauthenticator } from "@/services/api";

interface RequestCall {
  url: string;
  timeout?: number;
  header?: Record<string, string>;
}

const calls: RequestCall[] = [];
let responder: (call: RequestCall) => {
  statusCode: number;
  data: unknown;
};

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com");
  calls.length = 0;
  setReauthenticator(null);
  responder = () => ({ statusCode: 200, data: { ok: true } });
  vi.stubGlobal("uni", {
    request(options: RequestCall & {
      success: (result: { statusCode: number; data: unknown }) => void;
      fail: (error: { errMsg: string }) => void;
    }) {
      calls.push({
        url: options.url,
        timeout: options.timeout,
        header: options.header,
      });
      const result = responder(options);
      options.success(result);
      return { abort: vi.fn() };
    },
  });
});

describe("request timeout", () => {
  it("applies a default timeout shorter than the platform default", async () => {
    // 微信默认 60 秒，轮询场景下慢请求会与后续轮询叠加堆积。
    await apiRequest({ path: "/api/v1/me", accessToken: "token" });

    expect(calls[0]?.timeout).toBe(15_000);
  });

  it("lets callers override the timeout", async () => {
    await apiRequest({ path: "/api/v1/me", timeoutMs: 3_000 });

    expect(calls[0]?.timeout).toBe(3_000);
  });
});

describe("credential rejection recovery", () => {
  it("re-authenticates once and retries the original request on 401", async () => {
    // 核心回归：本地 expiresAt 未过期但服务端已拒绝（密钥轮换、凭据撤销、
    // 时钟偏移）。不重新登录的话所有请求会持续 401 直到本地时间自然过期。
    responder = (call) =>
      call.header?.Authorization === "Bearer fresh-token"
        ? { statusCode: 200, data: { id: "user-1" } }
        : { statusCode: 401, data: { error: { code: "INVALID_TOKEN" } } };
    const renew = vi.fn(async () => "fresh-token");
    setReauthenticator(renew);

    const result = await apiRequest<{ id: string }>({
      path: "/api/v1/me",
      accessToken: "stale-token",
    });

    expect(result).toEqual({ id: "user-1" });
    expect(renew).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(2);
  });

  it("recovers from 403 as well", async () => {
    responder = (call) =>
      call.header?.Authorization === "Bearer fresh-token"
        ? { statusCode: 200, data: { ok: true } }
        : { statusCode: 403, data: {} };
    setReauthenticator(async () => "fresh-token");

    await expect(
      apiRequest({ path: "/api/v1/me", accessToken: "stale-token" }),
    ).resolves.toEqual({ ok: true });
  });

  it("never retries more than once", async () => {
    // 服务端持续 401 时必须放弃，否则重试自身就成了新的失败循环。
    responder = () => ({ statusCode: 401, data: {} });
    const renew = vi.fn(async () => "fresh-token");
    setReauthenticator(renew);

    await expect(
      apiRequest({ path: "/api/v1/me", accessToken: "stale-token" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(renew).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(2);
  });

  it("does not retry when re-authentication fails", async () => {
    responder = () => ({ statusCode: 401, data: {} });
    setReauthenticator(async () => null);

    await expect(
      apiRequest({ path: "/api/v1/me", accessToken: "stale-token" }),
    ).rejects.toMatchObject({ statusCode: 401 });
    expect(calls).toHaveLength(1);
  });

  it("does not retry anonymous requests", async () => {
    // 登录接口本身不带凭据，重试它只会递归登录。
    responder = () => ({ statusCode: 401, data: {} });
    const renew = vi.fn(async () => "fresh-token");
    setReauthenticator(renew);

    await expect(
      apiRequest({ path: "/api/v1/auth/wechat/login", method: "POST" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(renew).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
  });

  it("leaves other failures untouched", async () => {
    responder = () => ({
      statusCode: 500,
      data: { error: { code: "INTERNAL", message: "服务暂时不可用。" } },
    });
    const renew = vi.fn(async () => "fresh-token");
    setReauthenticator(renew);

    await expect(
      apiRequest({ path: "/api/v1/me", accessToken: "token" }),
    ).rejects.toMatchObject({ code: "INTERNAL" });
    expect(renew).not.toHaveBeenCalled();
  });

  it("works when no reauthenticator is registered", async () => {
    responder = () => ({ statusCode: 401, data: {} });

    await expect(
      apiRequest({ path: "/api/v1/me", accessToken: "token" }),
    ).rejects.toMatchObject({ statusCode: 401 });
    expect(calls).toHaveLength(1);
  });
});
