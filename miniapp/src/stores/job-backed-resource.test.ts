import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createJobBackedResourceStore } from "@/stores/job-backed-resource";
import { useJobStore } from "@/stores/jobs";

interface Resource {
  id: string;
  jobId: string | null;
  complete: boolean;
}

interface State {
  current: Resource | null;
  activeJobId: string | null;
  activeId: string | null;
  recentId: string | null;
  idsByJob: Record<string, string>;
}

const storage = new Map<string, unknown>();
const resources = createJobBackedResourceStore<
  State,
  Resource,
  Omit<State, "current">
>({
  storageKey: "aiw:test-resources:v1",
  maxJobMappings: 2,
  resourceId: (resource) => resource.id,
  jobId: (resource) => resource.jobId,
  isComplete: (resource) => resource.complete,
  restore: (state, persisted) => {
    Object.assign(state, persisted);
  },
  serialize: ({ activeJobId, activeId, recentId, idsByJob }) => ({
    activeJobId,
    activeId,
    recentId,
    idsByJob,
  }),
  activeResourceId: (state, resourceId) => {
    state.activeId = resourceId;
  },
  recentResourceId: (state, resourceId) => {
    state.recentId = resourceId;
  },
  resourceIdsByJob: (state) => state.idsByJob,
  updateResourceIdsByJob: (state, idsByJob) => {
    state.idsByJob = idsByJob;
  },
});

function state(): State {
  return {
    current: null,
    activeJobId: null,
    activeId: null,
    recentId: null,
    idsByJob: {},
  };
}

describe("createJobBackedResourceStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    storage.clear();
    vi.stubGlobal("uni", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => {
        storage.set(key, value);
      },
    });
  });

  it("accepts, tracks and persists a completed resource", () => {
    const target = state();

    resources.accept(target, {
      id: "resource-1",
      jobId: "job-1",
      complete: true,
    });

    expect(target.activeId).toBe("resource-1");
    expect(target.recentId).toBe("resource-1");
    expect(target.idsByJob).toEqual({ "job-1": "resource-1" });
    expect(useJobStore().trackedJobIds).toEqual(["job-1"]);
    expect(storage.get("aiw:test-resources:v1")).toEqual({
      activeJobId: "job-1",
      activeId: "resource-1",
      recentId: "resource-1",
      idsByJob: { "job-1": "resource-1" },
    });
  });

  it("keeps the newest bounded job mappings", () => {
    const target = state();
    for (const id of ["1", "2", "3"]) {
      resources.accept(target, {
        id: `resource-${id}`,
        jobId: `job-${id}`,
        complete: false,
      });
    }

    expect(target.idsByJob).toEqual({
      "job-3": "resource-3",
      "job-2": "resource-2",
    });
    expect(resources.resourceIdForJob(target, "job-1")).toBeNull();
  });

  it("accepts a resource without inventing a job mapping", () => {
    const target = state();

    resources.accept(target, {
      id: "public-resource",
      jobId: null,
      complete: true,
    });

    expect(target.activeId).toBe("public-resource");
    expect(target.activeJobId).toBeNull();
    expect(target.recentId).toBe("public-resource");
    expect(target.idsByJob).toEqual({});
    expect(useJobStore().trackedJobIds).toEqual([]);
  });

  it("restores persisted state without reviving the resource body", () => {
    storage.set("aiw:test-resources:v1", {
      activeJobId: "job-1",
      activeId: "resource-1",
      recentId: "resource-1",
      idsByJob: { "job-1": "resource-1" },
    });
    const target = state();

    resources.hydrate(target);

    expect(target.current).toBeNull();
    expect(target.activeId).toBe("resource-1");
    expect(target.recentId).toBe("resource-1");
    expect(target.idsByJob).toEqual({ "job-1": "resource-1" });
  });
});
