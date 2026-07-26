import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/services/api";
import { createDiagnosis, type Diagnosis } from "@/services/diagnoses";
import {
  createOptimization,
  type Optimization,
} from "@/services/optimizations";
import {
  createShare,
  type Share,
  submitVote,
} from "@/services/shares";
import { useAuthStore } from "@/stores/auth";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { useShareStore } from "@/stores/shares";

vi.mock("@/services/diagnoses", () => ({
  createDiagnosis: vi.fn(),
  getDiagnosis: vi.fn(),
}));
vi.mock("@/services/optimizations", () => ({
  createOptimization: vi.fn(),
  getOptimization: vi.fn(),
}));
vi.mock("@/services/shares", () => ({
  createShare: vi.fn(),
  getShare: vi.fn(),
  recordShareContinue: vi.fn(),
  submitVote: vi.fn(),
}));

const createDiagnosisMock = vi.mocked(createDiagnosis);
const createOptimizationMock = vi.mocked(createOptimization);
const createShareMock = vi.mocked(createShare);
const submitVoteMock = vi.mocked(submitVote);
const storage = new Map<string, unknown>();

const diagnosis: Diagnosis = {
  id: "diagnosis-1",
  job_id: "diagnosis-job-1",
  occasion: "WORK",
  status: "COMPLETED",
  job_status: "COMPLETED",
  result: {
    input_quality: "ACCEPTABLE",
    input_quality_message: null,
    score: 82,
    summary: "整体利落，比例还有轻量优化空间。",
    strengths: [],
    issues: [],
    primary_issue: null,
    optimization_plan: [],
    disclaimer: "AI 建议仅供参考。",
  },
  error_code: null,
  user_message: null,
  created_at: "2026-07-26T12:00:00Z",
  updated_at: "2026-07-26T12:00:20Z",
  reused: false,
  quota_remaining: 2,
};

const optimization: Optimization = {
  id: "optimization-1",
  diagnosis_id: diagnosis.id,
  job_id: "optimization-job-1",
  status: "COMPLETED",
  job_status: "COMPLETED",
  change_level: 1,
  changes: [],
  quality_passed: true,
  critic_first_pass: true,
  before_image_url: "https://signed.example/before",
  after_image_url: "https://signed.example/after",
  error_code: null,
  user_message: null,
  created_at: "2026-07-26T12:01:00Z",
  updated_at: "2026-07-26T12:01:30Z",
  reused: false,
  quota_remaining: 1,
};

const share: Share = {
  id: "share-1",
  scene_code: "scene-code-1234567890",
  job_id: "share-job-1",
  status: "ACTIVE",
  job_status: "COMPLETED",
  card_url: "https://signed.example/share",
  change_level: 1,
  changes: [],
  score: 82,
  ai_edited: true,
  votes: { before: 0, after: 0 },
  viewer_choice: null,
  expires_at: null,
  error_code: null,
  user_message: null,
  reused: false,
};

function authenticate() {
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.accessToken = "access-token";
  auth.expiresAt = Date.now() + 60_000;
  auth.userId = "user-1";
}

describe("MVP core journey recovery", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    storage.clear();
    vi.clearAllMocks();
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    });
    authenticate();
  });

  it("reuses the diagnosis idempotency key after a network failure and restart", async () => {
    createDiagnosisMock
      .mockRejectedValueOnce(
        new ApiError("NETWORK_ERROR", "network unavailable", 0),
      )
      .mockResolvedValueOnce(diagnosis);
    const firstStore = useDiagnosisStore();

    await expect(firstStore.create("asset-1", "WORK")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
    });
    const firstKey = createDiagnosisMock.mock.calls[0]?.[1];

    setActivePinia(createPinia());
    authenticate();
    const restoredStore = useDiagnosisStore();
    restoredStore.hydrate();
    await restoredStore.create("asset-1", "WORK");

    expect(firstKey).toBeTruthy();
    expect(createDiagnosisMock.mock.calls[1]?.[1]).toBe(firstKey);
    expect(restoredStore.pendingRequest).toBeNull();
    expect(restoredStore.recentDiagnosisId).toBe(diagnosis.id);
  });

  it("persists diagnosis, optimization, share and vote state across the core journey", async () => {
    createDiagnosisMock.mockResolvedValueOnce(diagnosis);
    createOptimizationMock.mockResolvedValueOnce(optimization);
    createShareMock.mockResolvedValueOnce(share);
    submitVoteMock.mockResolvedValueOnce({
      scene_code: share.scene_code,
      choice: "AFTER",
      votes: { before: 0, after: 1 },
      reused: false,
    });

    await useDiagnosisStore().create("asset-1", "WORK");
    await useOptimizationStore().create(diagnosis.id);
    const shares = useShareStore();
    await shares.create(optimization.id, true);
    await shares.vote("AFTER");

    expect(shares.current?.viewer_choice).toBe("AFTER");
    expect(shares.current?.votes.after).toBe(1);
    expect(useJobStore().trackedJobIds).toEqual([
      share.job_id,
      optimization.job_id,
      diagnosis.job_id,
    ]);

    setActivePinia(createPinia());
    authenticate();
    const restoredDiagnoses = useDiagnosisStore();
    const restoredOptimizations = useOptimizationStore();
    const restoredShares = useShareStore();
    const restoredJobs = useJobStore();
    restoredDiagnoses.hydrate();
    restoredOptimizations.hydrate();
    restoredShares.hydrate();
    restoredJobs.hydrate();

    expect(restoredDiagnoses.diagnosisIdForJob(diagnosis.job_id)).toBe(
      diagnosis.id,
    );
    expect(
      restoredOptimizations.optimizationIdForJob(optimization.job_id),
    ).toBe(optimization.id);
    expect(restoredShares.sceneCodeForJob(share.job_id!)).toBe(
      share.scene_code,
    );
    expect(restoredJobs.trackedJobIds).toEqual([
      share.job_id,
      optimization.job_id,
      diagnosis.job_id,
    ]);
  });
});
