<script setup lang="ts">
import { onLoad } from "@dcloudio/uni-app";
import { computed } from "vue";
import { storeToRefs } from "pinia";

import { useJobPolling } from "@/composables/useJobPolling";
import { presentJobStage } from "@/lib/job-progress";
import { useAssetStore } from "@/stores/assets";
import { useDiagnosisStore } from "@/stores/diagnoses";

const diagnoses = useDiagnosisStore();
const assets = useAssetStore();
const { current, errorMessage, refreshing, submitting } = storeToRefs(diagnoses);
let diagnosisId: string | null = null;

const presentation = computed(() =>
  current.value
    ? presentJobStage(current.value.job_status)
    : { progress: 8, label: "正在恢复任务", recoverable: true },
);

const isFinalFailure = computed(() =>
  ["FAILED_FINAL", "CANCELLED"].includes(current.value?.job_status ?? ""),
);

const openResult = () => {
  if (!current.value) {
    return;
  }
  uni.redirectTo({ url: `/pages/diagnosis/result?id=${current.value.id}` });
};

const openTasks = () => {
  uni.navigateTo({ url: "/pages/tasks/index" });
};

const openHome = () => {
  uni.reLaunch({ url: "/pages/index/index" });
};

const poller = useJobPolling({
  canStart: () => Boolean(diagnosisId),
  poll: () => diagnoses.refresh(diagnosisId ?? undefined),
  evaluate: (diagnosis) => {
    if (!diagnosis) {
      return { continuePolling: true };
    }
    if (diagnosis.job_status === "COMPLETED") {
      openResult();
      return {
        continuePolling: false,
        progressKey: diagnosis.job_status,
      };
    }
    return {
      continuePolling: !["FAILED_FINAL", "TIMED_OUT", "CANCELLED"].includes(
        diagnosis.job_status,
      ),
      progressKey: diagnosis.job_status,
    };
  },
});

const retry = async () => {
  if (!current.value || !assets.assetId) {
    uni.showToast({ title: "请重新选择照片", icon: "none" });
    openHome();
    return;
  }
  try {
    const diagnosis = await diagnoses.create(
      assets.assetId,
      current.value.occasion,
      true,
    );
    diagnosisId = diagnosis.id;
    poller.start();
  } catch {
    // The store preserves a safe, user-facing error and the idempotency key.
  }
};

onLoad((query) => {
  diagnosisId =
    (typeof query?.id === "string" ? query.id : null) ??
    diagnoses.activeDiagnosisId;
});
</script>

<template>
  <view class="progress-page">
    <view class="ambient" />
    <header class="heading">
      <text class="heading__eyebrow">ANALYSIS IN PROGRESS</text>
      <text class="heading__title">先让 AI 看懂，{{ "\n" }}再给你建议。</text>
      <text class="heading__copy">
        你可以离开这个页面，任务会继续在后台处理。
      </text>
    </header>

    <main class="progress-card" aria-live="polite">
      <view class="progress-card__score">
        <text>{{ presentation.progress }}</text>
        <text class="progress-card__unit">%</text>
      </view>
      <text class="progress-card__label">{{
        current?.user_message || presentation.label
      }}</text>
      <view
        v-if="!isFinalFailure"
        class="progress-track"
        :aria-label="`诊断进度 ${presentation.progress}%`"
      >
        <view
          class="progress-track__fill"
          :style="{ width: `${presentation.progress}%` }"
        />
      </view>

      <view class="stage-list">
        <view
          v-for="stage in [
            { threshold: 8, label: '安全读取照片' },
            { threshold: 52, label: '理解比例、色彩与层次' },
            { threshold: 82, label: '检查建议是否具体、友善' },
          ]"
          :key="stage.threshold"
          class="stage"
          :class="{
            'stage--active': presentation.progress >= stage.threshold,
          }"
        >
          <text class="stage__mark">{{
            presentation.progress >= stage.threshold ? "✓" : "·"
          }}</text>
          <text>{{ stage.label }}</text>
        </view>
      </view>

      <text v-if="errorMessage" class="error-message">{{ errorMessage }}</text>

      <button
        v-if="isFinalFailure"
        class="primary-action"
        :disabled="submitting"
        @click="retry"
      >
        {{ submitting ? "正在重新创建" : "免费重试" }}
      </button>
      <button
        v-else-if="current?.job_status === 'TIMED_OUT'"
        class="primary-action"
        @click="openTasks"
      >
        去任务中心等待
      </button>
      <button v-else class="secondary-action" @click="openTasks">
        先离开，去任务中心
      </button>
      <button
        v-if="errorMessage && !refreshing && !isFinalFailure"
        class="text-action"
        @click="poller.refreshNow"
      >
        立即刷新
      </button>
    </main>

    <view class="promise">
      <text class="promise__mark">0</text>
      <view>
        <text class="promise__title">失败不消耗免费次数</text>
        <text class="promise__copy">系统重试有上限，不会让任务无限等待。</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.progress-page {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  padding: 54rpx 32rpx 80rpx;
  background: $color-paper;
}

.ambient {
  position: absolute;
  top: -240rpx;
  right: -240rpx;
  width: 560rpx;
  height: 560rpx;
  border-radius: 50%;
  background: rgba($color-sage, 0.22);
}

.heading {
  position: relative;
  z-index: 1;

  &__eyebrow {
    display: block;
    color: $color-vermilion;
    font-size: 20rpx;
    font-weight: 700;
    letter-spacing: 3rpx;
  }

  &__title {
    display: block;
    margin-top: 22rpx;
    color: $color-ink;
    font-family: "Songti SC", serif;
    font-size: 56rpx;
    font-weight: 700;
    line-height: 1.25;
  }

  &__copy {
    display: block;
    margin-top: 20rpx;
    color: $color-muted;
    font-size: 23rpx;
    line-height: 1.6;
  }
}

.progress-card {
  position: relative;
  z-index: 1;
  margin-top: 54rpx;
  padding: 40rpx 32rpx 32rpx;
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: rgba($color-white, 0.94);
  box-shadow: $shadow-soft;

  &__score {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 104rpx;
    font-weight: 700;
    line-height: 1;
  }

  &__unit {
    margin-left: 8rpx;
    color: $color-muted;
    font-size: 30rpx;
    font-weight: 400;
  }

  &__label {
    display: block;
    min-height: 48rpx;
    margin-top: 20rpx;
    color: $color-ink;
    font-size: 28rpx;
    font-weight: 700;
  }
}

.progress-track {
  height: 12rpx;
  margin-top: 28rpx;
  overflow: hidden;
  border-radius: 999rpx;
  background: $color-paper-deep;

  &__fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, $color-sage-deep, $color-vermilion);
    transition: width 360ms ease;
  }
}

.stage-list {
  display: grid;
  gap: 22rpx;
  margin-top: 40rpx;
  padding-top: 32rpx;
  border-top: 1rpx solid rgba($color-ink, 0.09);
}

.stage {
  display: flex;
  gap: 18rpx;
  align-items: center;
  color: rgba($color-muted, 0.68);
  font-size: 23rpx;

  &--active {
    color: $color-ink;
  }

  &__mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 42rpx;
    height: 42rpx;
    border: 1rpx solid rgba($color-ink, 0.14);
    border-radius: 50%;
    color: $color-sage-deep;
    font-weight: 700;
  }
}

.error-message {
  display: block;
  margin-top: 28rpx;
  padding: 20rpx;
  border-radius: $radius-small;
  background: rgba($color-vermilion, 0.09);
  color: $color-vermilion;
  font-size: 21rpx;
  line-height: 1.55;
}

.primary-action,
.secondary-action {
  width: 100%;
  height: 96rpx;
  margin-top: 32rpx;
  border-radius: 999rpx;
  font-size: 26rpx;
  font-weight: 700;
  line-height: 96rpx;
}

.primary-action {
  background: $color-ink;
  color: $color-white;
}

.secondary-action {
  border: 1rpx solid rgba($color-ink, 0.16);
  background: transparent;
  color: $color-ink;
}

.text-action {
  width: 100%;
  min-height: 88rpx;
  margin-top: 12rpx;
  background: transparent;
  color: $color-vermilion;
  font-size: 22rpx;
  line-height: 88rpx;
}

.promise {
  position: relative;
  z-index: 1;
  display: flex;
  gap: 18rpx;
  align-items: center;
  margin-top: 28rpx;
  padding: 24rpx 26rpx;
  border: 1rpx solid rgba($color-sage-deep, 0.14);
  border-radius: $radius-medium;

  &__mark {
    color: $color-sage-deep;
    font-family: "Songti SC", serif;
    font-size: 48rpx;
    font-weight: 700;
  }

  &__title {
    display: block;
    color: $color-ink;
    font-size: 22rpx;
    font-weight: 700;
  }

  &__copy {
    display: block;
    margin-top: 6rpx;
    color: $color-muted;
    font-size: 19rpx;
  }
}
</style>
