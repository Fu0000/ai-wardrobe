import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ApiService from "@/services/api";
import { apiRequest } from "@/services/api";
import {
  completeUpload,
  createUploadTicket,
  getPhotoDeletionStatus,
  requestPhotoDeletion,
  type SelectedImage,
} from "@/services/assets";
import {
  createDiagnosis,
  getDiagnosis,
} from "@/services/diagnoses";
import { createFeedback } from "@/services/feedback";
import { getJob } from "@/services/jobs";
import {
  createOptimization,
  getOptimization,
} from "@/services/optimizations";
import {
  getAccountDeletionStatus,
  getProfile,
  requestAccountDeletion,
  updateProfile,
} from "@/services/profile";

vi.mock("@/services/api", async (importOriginal) => {
  const original = await importOriginal<typeof ApiService>();
  return {
    ...original,
    apiRequest: vi.fn(),
  };
});

const apiRequestMock = vi.mocked(apiRequest);

beforeEach(() => {
  apiRequestMock.mockReset();
  apiRequestMock.mockResolvedValue({} as never);
});

describe("diagnosis, optimization and job API contracts", () => {
  it("preserves write idempotency and scoped resource paths", async () => {
    await createDiagnosis(
      { asset_id: "asset-1", occasion: "WORK" },
      "diagnosis-key",
      "token",
    );
    await getDiagnosis("diagnosis-1", "token");
    await createOptimization(
      "diagnosis-1",
      "optimization-key",
      "token",
      2,
    );
    await getOptimization("optimization-1", "token");
    await getJob("job-1", "token");

    expect(apiRequestMock.mock.calls).toEqual([
      [
        {
          path: "/api/v1/style-diagnoses",
          method: "POST",
          body: { asset_id: "asset-1", occasion: "WORK" },
          accessToken: "token",
          headers: { "Idempotency-Key": "diagnosis-key" },
        },
      ],
      [
        {
          path: "/api/v1/style-diagnoses/diagnosis-1",
          accessToken: "token",
        },
      ],
      [
        {
          path: "/api/v1/style-diagnoses/diagnosis-1/optimizations",
          method: "POST",
          body: { max_change_level: 2 },
          accessToken: "token",
          headers: { "Idempotency-Key": "optimization-key" },
        },
      ],
      [
        {
          path: "/api/v1/style-optimizations/optimization-1",
          accessToken: "token",
        },
      ],
      [{ path: "/api/v1/jobs/job-1", accessToken: "token" }],
    ]);
  });
});

describe("asset API contracts", () => {
  it("keeps upload completion and deletion requests explicit", async () => {
    const image: SelectedImage = {
      localPath: "wxfile://draft.jpg",
      contentType: "image/jpeg",
      sizeBytes: 1_024,
      width: 800,
      height: 1_200,
    };
    const ticket = {
      asset_id: "asset-1",
      upload_url: "https://cos.example/upload",
      method: "PUT" as const,
      headers: { "Content-Type": "image/jpeg" },
      expires_at: "2026-07-28T12:00:00Z",
    };

    await createUploadTicket(image, "token");
    await completeUpload(ticket, "image/jpeg", "token");
    await requestPhotoDeletion("asset-1", "deletion-key", "token");
    await getPhotoDeletionStatus("asset-1", "token");

    expect(apiRequestMock.mock.calls).toEqual([
      [
        {
          path: "/api/v1/assets/upload-ticket",
          method: "POST",
          body: { content_type: "image/jpeg", size_bytes: 1_024 },
          accessToken: "token",
        },
      ],
      [
        {
          path: "/api/v1/assets/asset-1/complete",
          method: "POST",
          body: { content_type: "image/jpeg" },
          accessToken: "token",
        },
      ],
      [
        {
          path: "/api/v1/me/photos/asset-1",
          method: "DELETE",
          accessToken: "token",
          headers: { "Idempotency-Key": "deletion-key" },
        },
      ],
      [
        {
          path: "/api/v1/me/photos/asset-1/deletion-status",
          accessToken: "token",
        },
      ],
    ]);
  });
});

describe("profile, account deletion and feedback API contracts", () => {
  it("does not lose consent, deletion idempotency or feedback context", async () => {
    await getProfile("token");
    await updateProfile(
      {
        display_name: "测试用户",
        consent_version: "v1.0",
        has_ai_processing_consent: true,
      },
      "token",
    );
    await requestAccountDeletion("account-deletion-key", "token");
    await getAccountDeletionStatus("token");
    await createFeedback(
      {
        category: "AI_QUALITY",
        rating: 2,
        message: "优化结果改变了未授权修改的外套。",
        related_job_id: "job-1",
        page: "pages/optimization/result",
      },
      "feedback-key",
      "token",
    );

    expect(apiRequestMock.mock.calls).toEqual([
      [{ path: "/api/v1/me", accessToken: "token" }],
      [
        {
          path: "/api/v1/me/profile",
          method: "POST",
          body: {
            display_name: "测试用户",
            consent_version: "v1.0",
            has_ai_processing_consent: true,
          },
          accessToken: "token",
        },
      ],
      [
        {
          path: "/api/v1/me/deletion-request",
          method: "POST",
          accessToken: "token",
          headers: { "Idempotency-Key": "account-deletion-key" },
        },
      ],
      [{ path: "/api/v1/me/deletion-status", accessToken: "token" }],
      [
        {
          path: "/api/v1/feedback",
          method: "POST",
          body: {
            category: "AI_QUALITY",
            rating: 2,
            message: "优化结果改变了未授权修改的外套。",
            related_job_id: "job-1",
            page: "pages/optimization/result",
          },
          accessToken: "token",
          headers: { "Idempotency-Key": "feedback-key" },
        },
      ],
    ]);
  });
});
