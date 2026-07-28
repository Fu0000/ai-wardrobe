// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as FeedbackService from "@/services/feedback";
import type * as ProfileService from "@/services/profile";

const lifecycle = vi.hoisted(() => ({
  load: [] as Array<(query?: Record<string, string>) => void>,
}));
const poller = vi.hoisted(() => ({
  start: vi.fn(),
  refreshNow: vi.fn(),
  stop: vi.fn(),
}));

vi.mock("@dcloudio/uni-app", () => ({
  onLoad: (callback: (query?: Record<string, string>) => void) =>
    lifecycle.load.push(callback),
  onShow: vi.fn(),
  onHide: vi.fn(),
  onUnload: vi.fn(),
}));
vi.mock("@/composables/useJobPolling", () => ({
  useJobPolling: () => poller,
}));
vi.mock("@/services/feedback", async (importOriginal) => {
  const original = await importOriginal<typeof FeedbackService>();
  return {
    ...original,
    createFeedback: vi.fn(),
  };
});
vi.mock("@/services/profile", async (importOriginal) => {
  const original = await importOriginal<typeof ProfileService>();
  return {
    ...original,
    requestAccountDeletion: vi.fn(),
    getAccountDeletionStatus: vi.fn(),
  };
});

import DeletionPage from "@/pages/profile/deletion.vue";
import FeedbackPage from "@/pages/profile/feedback.vue";
import TasksPage from "@/pages/tasks/index.vue";
import { createFeedback } from "@/services/feedback";
import {
  requestAccountDeletion,
  type Deletion,
} from "@/services/profile";
import { useAuthStore } from "@/stores/auth";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { useShareStore } from "@/stores/shares";

const createFeedbackMock = vi.mocked(createFeedback);
const requestAccountDeletionMock = vi.mocked(requestAccountDeletion);
const storage = new Map<string, unknown>();
const navigateTo = vi.fn();
const reLaunch = vi.fn();
const showToast = vi.fn();
const modalRequests: Array<{
  success?: (result: { confirm: boolean }) => void;
}> = [];

function authenticatedPinia() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.status = "authenticated";
  auth.accessToken = "access-token";
  auth.expiresAt = Date.now() + 60_000;
  auth.userId = "user-1";
  return pinia;
}

beforeEach(() => {
  vi.clearAllMocks();
  lifecycle.load.length = 0;
  modalRequests.length = 0;
  storage.clear();
  vi.stubGlobal("uni", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
    getSystemInfoSync: () => ({
      appVersion: "1.2.3",
      platform: "android",
      system: "Android 15",
      version: "8.0.50",
    }),
    getNetworkType: ({
      success,
    }: {
      success: (result: { networkType: string }) => void;
    }) => success({ networkType: "wifi" }),
    showModal: (request: {
      success?: (result: { confirm: boolean }) => void;
    }) => modalRequests.push(request),
    showToast,
    navigateTo,
    reLaunch,
  });
});

describe("feedback page", () => {
  it("normalizes input and submits only redacted diagnostic context", async () => {
    const pinia = authenticatedPinia();
    createFeedbackMock.mockResolvedValue({
      id: "feedback-1",
      category: "AI_QUALITY",
      status: "NEW",
      rating: 4,
      message: "优化图改变了没有授权修改的外套版型。",
      related_job_id: "job-1",
      created_at: "2026-07-28T12:00:00Z",
      reused: false,
    });
    const wrapper = mount(FeedbackPage, {
      global: { plugins: [pinia] },
    });
    lifecycle.load[0]?.({
      jobId: "job-1",
      from: "pages/optimization/result",
      traceId: "a".repeat(32),
    });
    await wrapper.get(".message-input").setValue(
      "  优化图改变了   没有授权修改的外套版型。  ",
    );
    await wrapper.findAll(".rating-row button")[3]?.trigger("click");

    const submit = wrapper.get(".submit-action");
    expect(submit.attributes("disabled")).toBeUndefined();
    await submit.trigger("click");
    await flushPromises();

    expect(createFeedbackMock).toHaveBeenCalledWith(
      {
        category: "AI_QUALITY",
        rating: 4,
        message: "优化图改变了 没有授权修改的外套版型。",
        related_job_id: "job-1",
        page: "pages/optimization/result",
        trace_id: "a".repeat(32),
        app_version: "1.2.3",
        platform: "android",
        system_version: "Android 15",
        wechat_version: "8.0.50",
        network_type: "wifi",
      },
      expect.stringMatching(/^feedback-/),
      "access-token",
    );
    expect(wrapper.text()).toContain("反馈已经入列");
    expect(showToast).toHaveBeenCalledWith({
      title: "反馈已收到",
      icon: "success",
    });
    expect(storage.has("aiw:beta-feedback:v1")).toBe(false);
  });
});

describe("account deletion page", () => {
  it("requires two confirmations before starting the persistent job", async () => {
    const pinia = authenticatedPinia();
    const deletion: Deletion = {
      id: "deletion-1",
      job_id: "job-deletion-1",
      status: "PENDING",
      completed_steps: [],
      user_message: "删除请求已创建。",
      can_retry: false,
      requested_at: "2026-07-28T12:00:00Z",
      updated_at: "2026-07-28T12:00:00Z",
      reused: false,
    };
    requestAccountDeletionMock.mockResolvedValue(deletion);
    const wrapper = mount(DeletionPage, {
      global: { plugins: [pinia] },
    });

    await wrapper.get(".danger-action").trigger("click");
    expect(modalRequests).toHaveLength(1);
    modalRequests[0]?.success?.({ confirm: true });
    expect(modalRequests).toHaveLength(2);
    modalRequests[1]?.success?.({ confirm: true });
    await flushPromises();

    expect(requestAccountDeletionMock).toHaveBeenCalledWith(
      expect.stringMatching(/^account-deletion-/),
      "access-token",
    );
    expect(poller.start).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain("正在安全删除");
    expect(wrapper.text()).toContain("删除请求已创建");
  });
});

describe("task center page", () => {
  it("routes recoverable jobs to their domain pages", async () => {
    const pinia = authenticatedPinia();
    const jobs = useJobStore();
    jobs.$patch({
      trackedJobIds: ["job-diagnosis", "job-optimization", "job-share"],
      jobs: {
        "job-diagnosis": {
          id: "job-diagnosis",
          task_type: "STYLE_DIAGNOSIS",
          status: "COMPLETED",
          progress: 100,
          retry_count: 0,
          error_code: null,
          user_message: null,
          result_reference_type: "StyleDiagnosis",
          result_reference_id: "diagnosis-1",
          created_at: "2026-07-28T12:00:00Z",
          updated_at: "2026-07-28T12:00:20Z",
        },
        "job-optimization": {
          id: "job-optimization",
          task_type: "STYLE_OPTIMIZATION",
          status: "PROCESSING",
          progress: 48,
          retry_count: 0,
          error_code: null,
          user_message: "正在生成优化图。",
          result_reference_type: null,
          result_reference_id: null,
          created_at: "2026-07-28T12:01:00Z",
          updated_at: "2026-07-28T12:01:20Z",
        },
        "job-share": {
          id: "job-share",
          task_type: "SHARE_ASSET",
          status: "COMPLETED",
          progress: 100,
          retry_count: 0,
          error_code: null,
          user_message: null,
          result_reference_type: "ShareRecord",
          result_reference_id: "share-1",
          created_at: "2026-07-28T12:02:00Z",
          updated_at: "2026-07-28T12:02:20Z",
        },
      },
    });
    useDiagnosisStore().diagnosisIdsByJob = {
      "job-diagnosis": "diagnosis-1",
    };
    useOptimizationStore().optimizationIdsByJob = {
      "job-optimization": "optimization-1",
    };
    useShareStore().sceneCodesByJob = {
      "job-share": "scene_code_123456",
    };
    const wrapper = mount(TasksPage, {
      global: { plugins: [pinia] },
    });
    const cards = wrapper.findAll(".task-card");

    await cards[0]?.trigger("click");
    await cards[1]?.trigger("click");
    await cards[2]?.trigger("click");

    expect(navigateTo.mock.calls).toEqual([
      [{ url: "/pages/diagnosis/result?id=diagnosis-1" }],
      [{ url: "/pages/optimization/index?id=optimization-1" }],
      [{ url: "/pages/share/confirm?scene=scene_code_123456" }],
    ]);
  });
});
