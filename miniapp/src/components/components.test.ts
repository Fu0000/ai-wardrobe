// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ProgressTrack from "@/components/ProgressTrack.vue";
import StateCard from "@/components/StateCard.vue";

describe("ProgressTrack", () => {
  it("clamps progress and exposes an accessible value", () => {
    const wrapper = mount(ProgressTrack, {
      props: {
        value: 127.8,
        label: "优化进度",
        size: "prominent",
        tone: "contrast",
      },
    });

    const track = wrapper.get('[role="progressbar"]');
    expect(track.attributes("aria-label")).toBe("优化进度");
    expect(track.attributes("aria-valuenow")).toBe("100");
    expect(track.classes()).toContain("progress-track--prominent");
    expect(track.classes()).toContain("progress-track--contrast");
    expect(wrapper.get(".progress-track__fill").attributes("style")).toContain(
      "width: 100%",
    );
  });
});

describe("StateCard", () => {
  it("announces errors and emits the recovery action", async () => {
    const wrapper = mount(StateCard, {
      props: {
        kind: "error",
        tone: "dark",
        mark: "!",
        message: "分享卡片暂时无法打开。",
        note: "你的私有原图仍然安全。",
        actionLabel: "重新加载",
      },
    });

    expect(wrapper.attributes("role")).toBe("alert");
    expect(wrapper.attributes("aria-live")).toBe("assertive");
    expect(wrapper.classes()).toContain("state-card--dark");
    expect(wrapper.text()).toContain("你的私有原图仍然安全。");

    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("action")).toHaveLength(1);
  });

  it("renders a quiet loading state without an unnecessary action", () => {
    const wrapper = mount(StateCard, {
      props: {
        kind: "loading",
        density: "compact",
        message: "正在恢复诊断结果",
      },
    });

    expect(wrapper.attributes("role")).toBe("status");
    expect(wrapper.attributes("aria-live")).toBe("polite");
    expect(wrapper.find("button").exists()).toBe(false);
    expect(wrapper.classes()).toContain("state-card--compact");
  });

  it("keeps empty states visually distinct from failures", () => {
    const wrapper = mount(StateCard, {
      props: {
        kind: "empty",
        mark: "空",
        title: "还没有后台任务",
        message: "创建任务后会在这里继续。",
      },
    });

    expect(wrapper.attributes("role")).toBe("status");
    expect(wrapper.classes()).toContain("state-card--empty");
    expect(wrapper.text()).toContain("还没有后台任务");
    expect(wrapper.find("button").exists()).toBe(false);
  });
});
