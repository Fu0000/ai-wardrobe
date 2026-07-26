import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeUpload,
  createUploadTicket,
  requestPhotoDeletion,
  uploadImageToTicket,
} from "@/services/assets";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/services/assets", () => ({
  chooseSourceImage: vi.fn(),
  completeUpload: vi.fn(),
  createUploadTicket: vi.fn(),
  removeSavedImage: vi.fn(() => Promise.resolve()),
  requestPhotoDeletion: vi.fn(() => Promise.resolve()),
  uploadImageToTicket: vi.fn(),
}));

const createUploadTicketMock = vi.mocked(createUploadTicket);
const completeUploadMock = vi.mocked(completeUpload);
const uploadImageToTicketMock = vi.mocked(uploadImageToTicket);
const requestPhotoDeletionMock = vi.mocked(requestPhotoDeletion);

const storage = new Map<string, unknown>();

const ticket = {
  asset_id: "asset-1",
  upload_url: "https://signed.example/put",
  method: "PUT" as const,
  headers: {},
  expires_at: "2026-07-26T13:00:00Z",
};

/** 一个可由测试决定何时兑现的 Promise，用来精确控制取消发生的时机。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function primeDraft() {
  const assets = useAssetStore();
  assets.image = {
    localPath: "/tmp/draft.jpg",
    contentType: "image/jpeg",
    sizeBytes: 1024,
    width: 800,
    height: 1200,
  };
  return assets;
}

describe("upload cancellation", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    storage.clear();
    vi.clearAllMocks();
    // clearAllMocks 不会清空 mockReturnValueOnce 队列，残留会串到下个用例。
    createUploadTicketMock.mockReset();
    completeUploadMock.mockReset();
    uploadImageToTicketMock.mockReset();
    requestPhotoDeletionMock.mockReset();
    requestPhotoDeletionMock.mockResolvedValue(
      undefined as unknown as Awaited<ReturnType<typeof requestPhotoDeletion>>,
    );
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    const auth = useAuthStore();
    auth.status = "authenticated";
    auth.accessToken = "access-token";
    auth.expiresAt = Date.now() + 60_000;
    auth.userId = "user-1";
  });

  it("does not revive a cancelled upload when completion still succeeds", async () => {
    // 核心回归：completing 阶段无法中断，请求仍会成功返回。
    // 若不校验取消状态，已撤回的上传会被复活成 ready 并进入诊断流程。
    const completion = deferred<{
      id: string;
      status: "READY";
      content_type: string;
      width: number;
      height: number;
    }>();
    createUploadTicketMock.mockResolvedValue(ticket);
    uploadImageToTicketMock.mockReturnValue({
      promise: Promise.resolve(),
      abort: vi.fn(),
    });
    completeUploadMock.mockReturnValue(completion.promise);

    const assets = primeDraft();
    const pending = assets.upload();
    await vi.waitFor(() => expect(assets.phase).toBe("completing"));

    assets.cancelUpload();
    completion.resolve({
      id: "asset-1",
      status: "READY",
      content_type: "image/jpeg",
      width: 800,
      height: 1200,
    });
    await pending;

    expect(assets.phase).toBe("cancelled");
    expect(assets.errorMessage).toBe("已暂停上传，可以稍后重试。");
  });

  it("discards the server-side asset created after cancellation", async () => {
    // 用户已撤回，服务端却已建好资产：必须主动清理，否则留下孤儿资产。
    const completion = deferred<{
      id: string;
      status: "READY";
      content_type: string;
      width: number;
      height: number;
    }>();
    createUploadTicketMock.mockResolvedValue(ticket);
    uploadImageToTicketMock.mockReturnValue({
      promise: Promise.resolve(),
      abort: vi.fn(),
    });
    completeUploadMock.mockReturnValue(completion.promise);

    const assets = primeDraft();
    const pending = assets.upload();
    await vi.waitFor(() => expect(assets.phase).toBe("completing"));

    assets.cancelUpload();
    completion.resolve({
      id: "asset-1",
      status: "READY",
      content_type: "image/jpeg",
      width: 800,
      height: 1200,
    });
    await pending;

    expect(requestPhotoDeletionMock).toHaveBeenCalledTimes(1);
    expect(requestPhotoDeletionMock.mock.calls[0]?.[0]).toBe("asset-1");
  });

  it("stops before direct upload when cancelled during ticket creation", async () => {
    // 取得 Ticket 的请求同样不可中断，返回后不该再推进到 uploading。
    const ticketRequest = deferred<typeof ticket>();
    createUploadTicketMock.mockReturnValue(ticketRequest.promise);

    const assets = primeDraft();
    const pending = assets.upload();
    await vi.waitFor(() => expect(assets.phase).toBe("authorizing"));

    assets.cancelUpload();
    ticketRequest.resolve(ticket);
    await pending;

    expect(assets.phase).toBe("cancelled");
    expect(uploadImageToTicketMock).not.toHaveBeenCalled();
    expect(completeUploadMock).not.toHaveBeenCalled();
    expect(requestPhotoDeletionMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the cancelled phase when the upload fails after cancellation", async () => {
    createUploadTicketMock.mockResolvedValue(ticket);
    const upload = deferred<void>();
    uploadImageToTicketMock.mockReturnValue({
      promise: upload.promise,
      abort: vi.fn(),
    });

    const assets = primeDraft();
    const pending = assets.upload();
    await vi.waitFor(() => expect(assets.phase).toBe("uploading"));

    assets.cancelUpload();
    upload.reject(new Error("uploadFile:fail abort"));
    await pending;

    expect(assets.phase).toBe("cancelled");
    // 取消导致的失败不应覆盖为「上传失败」文案。
    expect(assets.errorMessage).toBe("已暂停上传，可以稍后重试。");
  });

  it("lets a fresh upload win over a superseded one", async () => {
    // 取消后立即重试：旧流程的迟到响应不得覆盖新流程的状态。
    const staleCompletion = deferred<{
      id: string;
      status: "READY";
      content_type: string;
      width: number;
      height: number;
    }>();
    createUploadTicketMock.mockResolvedValue(ticket);
    uploadImageToTicketMock.mockReturnValue({
      promise: Promise.resolve(),
      abort: vi.fn(),
    });
    completeUploadMock.mockReturnValueOnce(staleCompletion.promise);

    const assets = primeDraft();
    const stale = assets.upload();
    await vi.waitFor(() => expect(assets.phase).toBe("completing"));

    assets.cancelUpload();
    completeUploadMock.mockResolvedValueOnce({
      id: "asset-2",
      status: "READY",
      content_type: "image/jpeg",
      width: 800,
      height: 1200,
    });
    await assets.upload();

    staleCompletion.resolve({
      id: "asset-1",
      status: "READY",
      content_type: "image/jpeg",
      width: 800,
      height: 1200,
    });
    await stale;

    expect(assets.phase).toBe("ready");
    expect(assets.assetId).toBe("asset-2");
  });

  it("completes normally when nothing is cancelled", async () => {
    createUploadTicketMock.mockResolvedValue(ticket);
    uploadImageToTicketMock.mockReturnValue({
      promise: Promise.resolve(),
      abort: vi.fn(),
    });
    completeUploadMock.mockResolvedValue({
      id: "asset-1",
      status: "READY",
      content_type: "image/jpeg",
      width: 800,
      height: 1200,
    });

    const assets = primeDraft();
    await assets.upload();

    expect(assets.phase).toBe("ready");
    expect(assets.assetId).toBe("asset-1");
    expect(requestPhotoDeletionMock).not.toHaveBeenCalled();
  });
});
