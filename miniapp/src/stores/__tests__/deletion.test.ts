import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Deletion } from "@/services/profile";
import { useAssetStore } from "@/stores/assets";
import { useAuthStore } from "@/stores/auth";
import { useDeletionStore } from "@/stores/deletion";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { usePhotoDeletionStore } from "@/stores/photo-deletion";
import { useShareStore } from "@/stores/shares";

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

  it("resets every store holding user data, not just storage", async () => {
    // 清 storage 不清 Pinia：这些 store 在 onLaunch 时 hydrate 后常驻内存，
    // 不重置的话注销当次会话内仍能读到已删除用户的诊断与优化结果。
    const diagnoses = useDiagnosisStore();
    const optimizations = useOptimizationStore();
    const shares = useShareStore();
    const photoDeletion = usePhotoDeletionStore();
    const jobs = useJobStore();

    diagnoses.recentDiagnosisId = "diagnosis-1";
    diagnoses.current = { id: "diagnosis-1" } as never;
    optimizations.recentOptimizationId = "optimization-1";
    optimizations.current = { id: "optimization-1" } as never;
    shares.recentSceneCode = "scene-code-1234567890";
    shares.current = { id: "share-1" } as never;
    photoDeletion.assetId = "asset-1";
    photoDeletion.current = { id: "photo-deletion-1" } as never;
    jobs.trackedJobIds = ["job-1"];
    jobs.jobs = { "job-1": { id: "job-1" } as never };

    await useDeletionStore().accept(completedDeletion);

    expect(diagnoses.current).toBeNull();
    expect(diagnoses.recentDiagnosisId).toBeNull();
    expect(optimizations.current).toBeNull();
    expect(optimizations.recentOptimizationId).toBeNull();
    expect(shares.current).toBeNull();
    expect(shares.recentSceneCode).toBeNull();
    expect(photoDeletion.current).toBeNull();
    expect(photoDeletion.assetId).toBeNull();
    expect(jobs.trackedJobIds).toEqual([]);
    expect(jobs.jobs).toEqual({});
  });

  it("keeps stores clean when deletion is still in progress", async () => {
    // 仅 COMPLETED 才清理；处理中不得抹掉用户仍在查看的结果。
    const diagnoses = useDiagnosisStore();
    diagnoses.recentDiagnosisId = "diagnosis-1";

    await useDeletionStore().accept({
      ...completedDeletion,
      status: "PROCESSING",
    });

    expect(diagnoses.recentDiagnosisId).toBe("diagnosis-1");
  });
});
