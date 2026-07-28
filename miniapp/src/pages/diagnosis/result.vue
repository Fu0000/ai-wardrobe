<script setup lang="ts">
import { onLoad, onShow } from "@dcloudio/uni-app";
import { computed } from "vue";
import { storeToRefs } from "pinia";

import PrimaryAction from "@/components/PrimaryAction.vue";
import StateCard from "@/components/StateCard.vue";
import { feedbackPageUrl } from "@/lib/navigation";
import { type Occasion } from "@/services/diagnoses";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useOptimizationStore } from "@/stores/optimizations";

const diagnoses = useDiagnosisStore();
const optimizations = useOptimizationStore();
const { current, errorMessage, refreshing } = storeToRefs(diagnoses);
const {
  errorMessage: optimizationError,
  submitting: optimizationSubmitting,
} = storeToRefs(optimizations);
let diagnosisId: string | null = null;

const occasionLabels: Record<Occasion, string> = {
  DAILY: "日常休闲",
  SCHOOL: "上学",
  WORK: "通勤",
  INTERVIEW: "面试",
  DATE: "约会",
  SOCIAL: "聚会",
  TRAVEL: "旅行",
  OTHER: "其他",
};

const result = computed(() =>
  current.value?.id === diagnosisId ? current.value.result : null,
);
const occasionLabel = computed(() =>
  current.value ? occasionLabels[current.value.occasion] : "",
);

const refresh = () => {
  if (diagnosisId) {
    void diagnoses.refresh(diagnosisId);
  }
};

const requestOptimization = async () => {
  if (!current.value || optimizationSubmitting.value) {
    return;
  }
  try {
    const optimization = await optimizations.create(current.value.id);
    uni.navigateTo({ url: `/pages/optimization/index?id=${optimization.id}` });
  } catch {
    // The store exposes safe recovery copy below the action.
  }
};

const reportIssue = () => {
  uni.navigateTo({
    url: feedbackPageUrl(
      "pages/diagnosis/result",
      current.value?.job_id,
    ),
  });
};

onLoad((query) => {
  diagnosisId =
    (typeof query?.id === "string" ? query.id : null) ??
    diagnoses.recentDiagnosisId;
});
onShow(refresh);
</script>

<template>
  <view class="result-page">
    <header class="result-hero">
      <view class="result-hero__topline">
        <text>STYLE NOTE · {{ occasionLabel }}</text>
        <text>AI 建议</text>
      </view>

      <view v-if="result" class="score-block">
        <view>
          <text class="score-block__label">这次穿搭的完成度</text>
          <text class="score-block__summary">{{ result.summary }}</text>
        </view>
        <view class="score-block__score">
          <text>{{ result.score ?? "—" }}</text>
          <text class="score-block__unit">/100</text>
        </view>
      </view>
    </header>

    <StateCard
      v-if="refreshing && !result"
      kind="loading"
      density="spacious"
      spacing="inset"
      mark="…"
      message="正在恢复诊断结果"
    />

    <StateCard
      v-else-if="errorMessage && !result"
      kind="error"
      density="spacious"
      spacing="inset"
      mark="!"
      :message="errorMessage"
      action-label="重新加载"
      @action="refresh"
    />

    <main v-else-if="result" class="result-content">
      <section class="result-section strengths">
        <view class="section-heading">
          <text class="section-heading__index">01</text>
          <view>
            <text class="section-heading__eyebrow">WHAT WORKS</text>
            <text class="section-heading__title">值得保留的部分</text>
          </view>
        </view>
        <view class="point-list">
          <article
            v-for="point in result.strengths"
            :key="point.title"
            class="point-card"
          >
            <text class="point-card__title">{{ point.title }}</text>
            <text class="point-card__body">{{ point.explanation }}</text>
            <text class="point-card__evidence">依据：{{ point.visual_evidence }}</text>
          </article>
        </view>
      </section>

      <section v-if="result.primary_issue" class="result-section primary-issue">
        <view class="section-heading section-heading--light">
          <text class="section-heading__index">02</text>
          <view>
            <text class="section-heading__eyebrow">PRIMARY ISSUE</text>
            <text class="section-heading__title">最值得先改的一件事</text>
          </view>
        </view>
        <text class="primary-issue__title">{{ result.primary_issue.title }}</text>
        <text class="primary-issue__body">{{
          result.primary_issue.explanation
        }}</text>
        <view class="primary-issue__impact">
          <text>预期变化</text>
          <text>{{ result.primary_issue.expected_impact }}</text>
        </view>
      </section>

      <section class="result-section plan">
        <view class="section-heading">
          <text class="section-heading__index">03</text>
          <view>
            <text class="section-heading__eyebrow">MINIMAL CHANGE</text>
            <text class="section-heading__title">按这个顺序动手</text>
          </view>
        </view>
        <view class="plan-list">
          <article
            v-for="step in result.optimization_plan"
            :key="step.priority"
            class="plan-step"
          >
            <text class="plan-step__priority">0{{ step.priority }}</text>
            <view>
              <text class="plan-step__instruction">{{ step.instruction }}</text>
              <text class="plan-step__reason">{{ step.reason }}</text>
              <text class="plan-step__preserves">保留：{{ step.preserves }}</text>
            </view>
          </article>
        </view>
      </section>

      <PrimaryAction
        :label="
          optimizationSubmitting
            ? '正在创建优化任务'
            : '看看优化后是什么样'
        "
        :disabled="optimizationSubmitting"
        tone="accent"
        spacing="loose"
        @action="requestOptimization"
      />
      <text v-if="optimizationError" class="optimization-error">{{
        optimizationError
      }}</text>
      <text class="disclaimer">{{ result.disclaimer }}</text>
      <button class="feedback-action" @click="reportIssue">这份建议不准确</button>
    </main>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.result-page {
  min-height: 100vh;
  padding-bottom: 88rpx;
  background: $color-paper;
}

.result-hero {
  padding: 50rpx 32rpx 54rpx;
  background:
    radial-gradient(circle at 95% 5%, rgba($color-vermilion, 0.22), transparent 38%),
    $color-ink;
  color: $color-white;

  &__topline {
    display: flex;
    justify-content: space-between;
    color: rgba($color-white, 0.64);
    font-size: 18rpx;
    font-weight: 700;
    letter-spacing: 3rpx;
  }
}

.score-block {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 28rpx;
  align-items: end;
  margin-top: 64rpx;

  &__label {
    display: block;
    color: $color-sage;
    font-size: 21rpx;
    font-weight: 700;
  }

  &__summary {
    display: block;
    max-width: 470rpx;
    margin-top: 18rpx;
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
    line-height: 1.5;
  }

  &__score {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 92rpx;
    font-weight: 700;
    line-height: 0.9;
  }

  &__unit {
    margin-left: 4rpx;
    color: rgba($color-white, 0.52);
    font-size: 20rpx;
    font-weight: 400;
  }
}

.result-content {
  padding: 0 32rpx;
}

.result-section {
  margin-top: 32rpx;
  padding: 34rpx 30rpx;
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: $color-white;
}

.section-heading {
  display: flex;
  gap: 20rpx;
  align-items: center;

  &__index {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 42rpx;
    font-weight: 700;
  }

  &__eyebrow {
    display: block;
    color: $color-sage-deep;
    font-size: 17rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__title {
    display: block;
    margin-top: 5rpx;
    color: $color-ink;
    font-family: "Songti SC", serif;
    font-size: 29rpx;
    font-weight: 700;
  }

  &--light {
    .section-heading__eyebrow {
      color: $color-sage;
    }

    .section-heading__title {
      color: $color-white;
    }
  }
}

.point-list {
  display: grid;
  gap: 20rpx;
  margin-top: 30rpx;
}

.point-card {
  padding: 24rpx;
  border-radius: $radius-medium;
  background: $color-paper;

  &__title,
  &__body,
  &__evidence {
    display: block;
  }

  &__title {
    color: $color-ink;
    font-size: 25rpx;
    font-weight: 700;
  }

  &__body {
    margin-top: 12rpx;
    color: $color-muted;
    font-size: 22rpx;
    line-height: 1.65;
  }

  &__evidence {
    margin-top: 12rpx;
    color: $color-sage-deep;
    font-size: 19rpx;
    line-height: 1.5;
  }
}

.primary-issue {
  background: $color-ink;
  color: $color-white;

  &__title,
  &__body {
    display: block;
  }

  &__title {
    margin-top: 34rpx;
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 40rpx;
    font-weight: 700;
  }

  &__body {
    margin-top: 18rpx;
    color: rgba($color-white, 0.78);
    font-size: 23rpx;
    line-height: 1.75;
  }

  &__impact {
    display: grid;
    gap: 8rpx;
    margin-top: 26rpx;
    padding: 20rpx 22rpx;
    border-left: 4rpx solid $color-vermilion;
    background: rgba($color-white, 0.07);
    color: rgba($color-white, 0.7);
    font-size: 20rpx;
  }
}

.plan-list {
  display: grid;
  gap: 24rpx;
  margin-top: 32rpx;
}

.plan-step {
  display: grid;
  grid-template-columns: 58rpx 1fr;
  gap: 18rpx;
  padding-bottom: 24rpx;
  border-bottom: 1rpx solid rgba($color-ink, 0.09);

  &:last-child {
    padding-bottom: 0;
    border-bottom: 0;
  }

  &__priority {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 28rpx;
    font-weight: 700;
  }

  &__instruction,
  &__reason,
  &__preserves {
    display: block;
  }

  &__instruction {
    color: $color-ink;
    font-size: 25rpx;
    font-weight: 700;
    line-height: 1.5;
  }

  &__reason {
    margin-top: 10rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.65;
  }

  &__preserves {
    margin-top: 10rpx;
    color: $color-sage-deep;
    font-size: 19rpx;
  }
}

.disclaimer {
  display: block;
  margin-top: 24rpx;
  color: $color-muted;
  font-size: 20rpx;
  line-height: 1.55;
  text-align: center;
}

.optimization-error {
  display: block;
  margin-top: 18rpx;
  color: $color-vermilion;
  font-size: 20rpx;
  line-height: 1.55;
  text-align: center;
}

.feedback-action {
  width: 100%;
  min-height: 88rpx;
  margin-top: 6rpx;
  background: transparent;
  color: $color-muted;
  font-size: 21rpx;
  line-height: 88rpx;
}
</style>
