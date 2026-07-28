<script setup lang="ts">
import { onLoad } from "@dcloudio/uni-app";
import { computed } from "vue";
import { storeToRefs } from "pinia";

import { useJobPolling } from "@/composables/useJobPolling";
import { presentOptimizationStage } from "@/lib/job-progress";
import { useOptimizationStore } from "@/stores/optimizations";

const optimizations = useOptimizationStore();
const { current, errorMessage, refreshing, submitting } =
  storeToRefs(optimizations);
let optimizationId: string | null = null;

const presentation = computed(() =>
  current.value
    ? presentOptimizationStage(current.value.job_status)
    : { progress: 8, label: "正在恢复任务", recoverable: true },
);
const isFinalFailure = computed(() =>
  ["FAILED_FINAL", "CANCELLED"].includes(current.value?.job_status ?? ""),
);

const openResult = () => {
  if (!current.value) {
    return;
  }
  uni.redirectTo({ url: `/pages/optimization/result?id=${current.value.id}` });
};

const openTasks = () => {
  uni.navigateTo({ url: "/pages/tasks/index" });
};

const poller = useJobPolling({
  canStart: () => Boolean(optimizationId),
  poll: () => optimizations.refresh(optimizationId ?? undefined),
  evaluate: (optimization) => {
    if (!optimization) {
      return { continuePolling: true };
    }
    if (optimization.job_status === "COMPLETED") {
      openResult();
      return {
        continuePolling: false,
        progressKey: optimization.job_status,
      };
    }
    return {
      continuePolling: !["FAILED_FINAL", "TIMED_OUT", "CANCELLED"].includes(
        optimization.job_status,
      ),
      progressKey: optimization.job_status,
    };
  },
});

const retry = async () => {
  if (!current.value) {
    return;
  }
  try {
    const optimization = await optimizations.create(
      current.value.diagnosis_id,
      true,
    );
    optimizationId = optimization.id;
    poller.start();
  } catch {
    // Safe error copy and the idempotency key are retained by the store.
  }
};

onLoad((query) => {
  optimizationId =
    (typeof query?.id === "string" ? query.id : null) ??
    optimizations.activeOptimizationId;
});
</script>

<template>
  <view class="optimization-page">
    <header class="heading">
      <text class="heading__eyebrow">MINIMAL CHANGE · LEVEL {{ current?.change_level ?? "—" }}</text>
      <text class="heading__title">只改必要的，{{ "\n" }}其余都保留。</text>
      <text class="heading__copy">
        AI 正在生成 After，并检查人物身份、未修改衣物、背景和构图是否保持一致。
      </text>
    </header>

    <main class="progress-card" aria-live="polite">
      <view class="progress-orbit">
        <view
          class="progress-orbit__fill"
          :style="{ transform: `rotate(${presentation.progress * 3.6}deg)` }"
        />
        <view class="progress-orbit__center">
          <text>{{ presentation.progress }}</text>
          <text>%</text>
        </view>
      </view>
      <text class="progress-card__label">{{
        current?.user_message || presentation.label
      }}</text>

      <view class="guardrails">
        <view
          v-for="item in [
            '保持同一个人',
            '保持未修改衣物',
            '保持背景与构图',
            '异常结果不展示',
          ]"
          :key="item"
          class="guardrail"
        >
          <text class="guardrail__mark">✓</text>
          <text>{{ item }}</text>
        </view>
      </view>

      <text v-if="errorMessage" class="error-message">{{ errorMessage }}</text>

      <button
        v-if="isFinalFailure"
        class="primary-action"
        :disabled="submitting"
        @click="retry"
      >
        {{ submitting ? "正在重新创建" : "免费重新生成" }}
      </button>
      <button v-else class="secondary-action" @click="openTasks">
        先离开，任务会继续
      </button>
      <button
        v-if="errorMessage && !refreshing && !isFinalFailure"
        class="text-action"
        @click="poller.refreshNow"
      >
        立即刷新
      </button>
    </main>

    <view class="trust-note">
      <text class="trust-note__value">0</text>
      <text>图片或质量检查失败，不消耗免费次数。</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.optimization-page {
  min-height: 100vh;
  padding: 54rpx 32rpx 80rpx;
  background:
    radial-gradient(circle at 92% 2%, rgba($color-vermilion, 0.16), transparent 30%),
    $color-paper;
}

.heading {
  &__eyebrow {
    display: block;
    color: $color-vermilion;
    font-size: 19rpx;
    font-weight: 700;
    letter-spacing: 3rpx;
  }

  &__title {
    display: block;
    margin-top: 22rpx;
    color: $color-ink;
    font-family: "Songti SC", serif;
    font-size: 58rpx;
    font-weight: 700;
    line-height: 1.22;
  }

  &__copy {
    display: block;
    margin-top: 22rpx;
    color: $color-muted;
    font-size: 23rpx;
    line-height: 1.7;
  }
}

.progress-card {
  margin-top: 52rpx;
  padding: 44rpx 32rpx 32rpx;
  border-radius: $radius-large;
  background: $color-ink;
  color: $color-white;
  box-shadow: $shadow-soft;

  &__label {
    display: block;
    margin-top: 30rpx;
    font-family: "Songti SC", serif;
    font-size: 30rpx;
    font-weight: 700;
    text-align: center;
  }
}

.progress-orbit {
  position: relative;
  width: 220rpx;
  height: 220rpx;
  margin: 0 auto;
  overflow: hidden;
  border: 1rpx solid rgba($color-white, 0.18);
  border-radius: 50%;
  background: rgba($color-white, 0.05);

  &__fill {
    position: absolute;
    top: -20rpx;
    left: 50%;
    width: 4rpx;
    height: 130rpx;
    background: $color-vermilion;
    transform-origin: 0 130rpx;
    transition: transform 360ms ease;
  }

  &__center {
    position: absolute;
    inset: 24rpx;
    display: flex;
    align-items: baseline;
    justify-content: center;
    border-radius: 50%;
    background: $color-ink;
    color: $color-white;
    font-family: "Songti SC", serif;
    font-size: 70rpx;
    font-weight: 700;

    text:last-child {
      margin-left: 5rpx;
      color: $color-sage;
      font-size: 23rpx;
    }
  }
}

.guardrails {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16rpx;
  margin-top: 38rpx;
  padding-top: 30rpx;
  border-top: 1rpx solid rgba($color-white, 0.12);
}

.guardrail {
  display: flex;
  gap: 10rpx;
  align-items: center;
  color: rgba($color-white, 0.72);
  font-size: 20rpx;

  &__mark {
    color: $color-sage;
    font-weight: 700;
  }
}

.error-message {
  display: block;
  margin-top: 26rpx;
  padding: 18rpx;
  border-radius: $radius-small;
  background: rgba($color-vermilion, 0.14);
  color: #ffb7a8;
  font-size: 21rpx;
  line-height: 1.5;
}

.primary-action,
.secondary-action {
  width: 100%;
  height: 96rpx;
  margin-top: 32rpx;
  border-radius: 999rpx;
  font-size: 25rpx;
  font-weight: 700;
  line-height: 96rpx;
}

.primary-action {
  background: $color-vermilion;
  color: $color-white;
}

.secondary-action {
  border: 1rpx solid rgba($color-white, 0.22);
  background: transparent;
  color: $color-white;
}

.text-action {
  width: 100%;
  min-height: 88rpx;
  background: transparent;
  color: #ffb7a8;
  font-size: 21rpx;
  line-height: 88rpx;
}

.trust-note {
  display: flex;
  gap: 16rpx;
  align-items: center;
  margin-top: 26rpx;
  padding: 22rpx 24rpx;
  border: 1rpx solid rgba($color-sage-deep, 0.14);
  border-radius: $radius-medium;
  color: $color-muted;
  font-size: 20rpx;

  &__value {
    color: $color-sage-deep;
    font-family: "Songti SC", serif;
    font-size: 42rpx;
    font-weight: 700;
  }
}
</style>
