import { beforeEach, describe, expect, it, vi } from "vitest";

const lifecycle = vi.hoisted(() => ({
  show: [] as Array<() => void>,
  hide: [] as Array<() => void>,
  unload: [] as Array<() => void>,
}));

vi.mock("@dcloudio/uni-app", () => ({
  onShow: (callback: () => void) => lifecycle.show.push(callback),
  onHide: (callback: () => void) => lifecycle.hide.push(callback),
  onUnload: (callback: () => void) => lifecycle.unload.push(callback),
}));

import {
  useJobPolling,
  type JobPollResult,
} from "@/composables/useJobPolling";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe("useJobPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    lifecycle.show.length = 0;
    lifecycle.hide.length = 0;
    lifecycle.unload.length = 0;
  });

  it("owns page lifecycle and resets backoff when progress changes", async () => {
    const poll = vi
      .fn()
      .mockResolvedValueOnce({
        continuePolling: true,
        progressKey: "PROCESSING",
      })
      .mockResolvedValueOnce({
        continuePolling: true,
        progressKey: "PROCESSING",
      })
      .mockResolvedValueOnce({
        continuePolling: true,
        progressKey: "QUALITY_CHECKING",
      })
      .mockResolvedValueOnce({
        continuePolling: false,
        progressKey: "COMPLETED",
      });
    const controller = useJobPolling({
      poll,
      evaluate: (result: JobPollResult) => result,
    });

    lifecycle.show[0]?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(controller.isPolling.value).toBe(true);

    await vi.advanceTimersByTimeAsync(1_500);
    expect(poll).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2_500);
    expect(poll).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1_500);

    expect(poll).toHaveBeenCalledTimes(4);
    expect(controller.isPolling.value).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("fences an in-flight result after the page is hidden", async () => {
    const pending = deferred<{
      continuePolling: boolean;
      progressKey: string;
    }>();
    const poll = vi.fn(() => pending.promise);
    const controller = useJobPolling({
      poll,
      evaluate: (result: JobPollResult) => result,
    });

    lifecycle.show[0]?.();
    await vi.advanceTimersByTimeAsync(0);
    lifecycle.hide[0]?.();
    pending.resolve({
      continuePolling: true,
      progressKey: "PROCESSING",
    });
    await vi.advanceTimersByTimeAsync(20_000);

    expect(controller.isPolling.value).toBe(false);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("runs once after a hide-show race with an in-flight request", async () => {
    const first = deferred<{
      continuePolling: boolean;
      progressKey: string;
    }>();
    const poll = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue({
        continuePolling: false,
        progressKey: "COMPLETED",
      });
    const controller = useJobPolling({
      poll,
      evaluate: (result: JobPollResult) => result,
    });

    lifecycle.show[0]?.();
    await vi.advanceTimersByTimeAsync(0);
    lifecycle.hide[0]?.();
    lifecycle.show[0]?.();
    first.resolve({
      continuePolling: true,
      progressKey: "PROCESSING",
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(poll).toHaveBeenCalledTimes(2);
    expect(controller.isPolling.value).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not start until the resource identifier is available", async () => {
    let available = false;
    const poll = vi.fn().mockResolvedValue({ continuePolling: false });
    const controller = useJobPolling({
      poll,
      evaluate: (result: JobPollResult) => result,
      canStart: () => available,
    });

    lifecycle.show[0]?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).not.toHaveBeenCalled();

    available = true;
    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).toHaveBeenCalledOnce();
  });
});
