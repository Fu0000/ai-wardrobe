import { useJobStore } from "@/stores/jobs";

interface JobBackedState<TResource> {
  current: TResource | null;
  activeJobId: string | null;
}

interface JobBackedResourceStoreOptions<
  TState extends JobBackedState<TResource>,
  TResource,
  TPersisted,
> {
  storageKey: string;
  maxJobMappings?: number;
  resourceId: (resource: TResource) => string;
  jobId: (resource: TResource) => string | null;
  isComplete: (resource: TResource) => boolean;
  restore: (state: TState, persisted: TPersisted) => void;
  serialize: (state: TState) => TPersisted;
  activeResourceId: (state: TState, resourceId: string) => void;
  recentResourceId: (state: TState, resourceId: string) => void;
  resourceIdsByJob: (state: TState) => Record<string, string>;
  updateResourceIdsByJob: (
    state: TState,
    resourceIdsByJob: Record<string, string>,
  ) => void;
}

interface JobBackedResourceStore<
  TState,
  TResource,
> {
  hydrate: (state: TState) => void;
  accept: (state: TState, resource: TResource) => void;
  resourceIdForJob: (state: TState, jobId: string) => string | null;
  persist: (state: TState) => void;
}

export function createJobBackedResourceStore<
  TState extends JobBackedState<TResource>,
  TResource,
  TPersisted,
>(
  options: JobBackedResourceStoreOptions<TState, TResource, TPersisted>,
): JobBackedResourceStore<TState, TResource> {
  const maxJobMappings = options.maxJobMappings ?? 20;

  const persist = (state: TState) => {
    uni.setStorageSync(options.storageKey, options.serialize(state));
  };

  return {
    hydrate(state) {
      const persisted = uni.getStorageSync(options.storageKey) as
        | TPersisted
        | "";
      if (persisted) {
        options.restore(state, persisted);
      }
    },
    accept(state, resource) {
      const resourceId = options.resourceId(resource);
      const jobId = options.jobId(resource);
      state.current = resource;
      state.activeJobId = jobId;
      options.activeResourceId(state, resourceId);

      if (jobId) {
        const mappings = {
          [jobId]: resourceId,
          ...options.resourceIdsByJob(state),
        };
        options.updateResourceIdsByJob(
          state,
          Object.fromEntries(
            Object.entries(mappings).slice(0, maxJobMappings),
          ),
        );
        useJobStore().track(jobId);
      }
      if (options.isComplete(resource)) {
        options.recentResourceId(state, resourceId);
      }
      persist(state);
    },
    resourceIdForJob(state, jobId) {
      return options.resourceIdsByJob(state)[jobId] ?? null;
    },
    persist,
  };
}
