import { onHide, onShow, onUnload } from "@dcloudio/uni-app";
import { readonly, ref, type DeepReadonly, type Ref } from "vue";

import { nextJobPollDelay } from "@/lib/job-progress";

export interface JobPollResult {
  continuePolling: boolean;
  progressKey?: string | number | null;
}

interface JobPollingOptions<TResult> {
  poll: () => Promise<TResult>;
  evaluate: (result: TResult) => JobPollResult;
  canStart?: () => boolean;
  onError?: (error: unknown) => void;
}

interface JobPollingController {
  isPolling: DeepReadonly<Ref<boolean>>;
  start: () => void;
  refreshNow: () => void;
  stop: () => void;
}

const UNSET_PROGRESS = Symbol("unset-job-progress");

export function useJobPolling<TResult>(
  options: JobPollingOptions<TResult>,
): JobPollingController {
  const isPolling = ref(false);
  let timer: ReturnType<typeof globalThis.setTimeout> | null = null;
  let generation = 0;
  let pollAttempt = 0;
  let progressKey: JobPollResult["progressKey"] | typeof UNSET_PROGRESS =
    UNSET_PROGRESS;
  let inFlight = false;
  let immediateRunPending = false;

  const clearTimer = () => {
    if (timer === null) {
      return;
    }
    globalThis.clearTimeout(timer);
    timer = null;
  };

  const stop = () => {
    generation += 1;
    isPolling.value = false;
    immediateRunPending = false;
    clearTimer();
  };

  const runImmediately = () => {
    clearTimer();
    if (inFlight) {
      immediateRunPending = true;
      return;
    }
    void execute(generation);
  };

  const drainImmediateRun = () => {
    if (!immediateRunPending || !isPolling.value) {
      return;
    }
    immediateRunPending = false;
    runImmediately();
  };

  const schedule = (expectedGeneration: number) => {
    if (
      !isPolling.value ||
      generation !== expectedGeneration ||
      (options.canStart && !options.canStart())
    ) {
      stop();
      return;
    }
    const delay = nextJobPollDelay(pollAttempt);
    pollAttempt += 1;
    timer = globalThis.setTimeout(() => {
      timer = null;
      void execute(expectedGeneration);
    }, delay);
  };

  async function execute(expectedGeneration: number): Promise<void> {
    if (!isPolling.value || generation !== expectedGeneration) {
      return;
    }
    if (inFlight) {
      immediateRunPending = true;
      return;
    }

    inFlight = true;
    let polled: TResult;
    try {
      polled = await options.poll();
    } catch (error) {
      inFlight = false;
      if (isPolling.value && generation === expectedGeneration) {
        stop();
        options.onError?.(error);
      }
      drainImmediateRun();
      return;
    }
    inFlight = false;

    if (!isPolling.value || generation !== expectedGeneration) {
      drainImmediateRun();
      return;
    }
    const result = options.evaluate(polled);
    if (
      Object.hasOwn(result, "progressKey") &&
      result.progressKey !== progressKey
    ) {
      progressKey = result.progressKey;
      pollAttempt = 0;
    }
    if (!result.continuePolling) {
      stop();
      return;
    }
    if (immediateRunPending) {
      drainImmediateRun();
      return;
    }
    schedule(expectedGeneration);
  }

  const start = () => {
    if (options.canStart && !options.canStart()) {
      stop();
      return;
    }
    generation += 1;
    isPolling.value = true;
    pollAttempt = 0;
    progressKey = UNSET_PROGRESS;
    runImmediately();
  };

  const refreshNow = () => {
    if (!isPolling.value) {
      start();
      return;
    }
    runImmediately();
  };

  onShow(start);
  onHide(stop);
  onUnload(stop);

  return {
    isPolling: readonly(isPolling),
    start,
    refreshNow,
    stop,
  };
}
