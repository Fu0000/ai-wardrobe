import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Deletion } from "@/services/profile";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";
import { useDeletionStore } from "@/stores/deletion";

const completedDeletion: Deletion = {
  id: "deletion-1",
  job_id: "job-1",
  status: "COMPLETED",
  completed_steps: ["database", "objects"],
  user_message: "账号和相关数据已删除。",
  can_retry: false,
  requested_at: "2026-07-26T12:00:00Z",
  updated_at: "2026-07-26T12:01:00Z",
  reused: false,
};

describe("account deletion local privacy cleanup", () => {
  const storage = new Map<string, unknown>();
  const removeSavedFile = vi.fn(
    (options: { filePath: string; complete: () => void }) => {
      options.complete();
    },
  );

  beforeEach(() => {
    setActivePinia(createPinia());
    storage.clear();
    storage.set("aiw:auth:v1", { accessToken: "token" });
    storage.set("aiw:source-draft:v1", { private: true });
    storage.set("aiw:tracked-jobs:v1", ["job-1"]);
    removeSavedFile.mockClear();
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
      getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
      removeSavedFile,
    });
  });

  it("removes the saved photo before clearing local account state", async () => {
    const assets = useAssetStore();
    assets.image = {
      localPath: "wxfile://private-photo.jpg",
      contentType: "image/jpeg",
      sizeBytes: 1024,
      width: 640,
      height: 960,
    };
    assets.assetId = "asset-1";
    const auth = useAuthStore();
    auth.status = "authenticated";
    auth.accessToken = "token";
    auth.expiresAt = Date.now() + 60_000;
    auth.userId = "user-1";

    await useDeletionStore().accept(completedDeletion);

    expect(removeSavedFile).toHaveBeenCalledWith(
      expect.objectContaining({ filePath: "wxfile://private-photo.jpg" }),
    );
    expect(assets.image).toBeNull();
    expect(auth.accessToken).toBeNull();
    expect([...storage.keys()]).toEqual(["aiw:deletion-completed:v1"]);
  });
});
