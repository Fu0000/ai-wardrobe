import { describe, expect, it } from "vitest";

import {
  nextJobPollDelay,
  presentJobStage,
  presentOptimizationStage,
} from "./job-progress";

describe("presentJobStage", () => {
  it("keeps processing jobs recoverable when the page closes", () => {
    expect(presentJobStage("PROCESSING")).toEqual({
      progress: 52,
      label: "AI 正在理解你的穿搭",
      recoverable: true,
    });
  });

  it("marks completed jobs as terminal", () => {
    expect(presentJobStage("COMPLETED").recoverable).toBe(false);
    expect(presentJobStage("COMPLETED").progress).toBe(100);
  });

  it("backs polling off and caps the interval", () => {
    expect([0, 1, 2, 3, 4, 5, 10].map(nextJobPollDelay)).toEqual([
      1_500, 2_500, 4_000, 6_500, 10_000, 15_000, 15_000,
    ]);
  });

  it("uses optimization-specific progress copy", () => {
    expect(presentOptimizationStage("QUALITY_CHECKING")).toEqual({
      progress: 86,
      label: "正在检查身份与衣物一致性",
      recoverable: true,
    });
  });
});
