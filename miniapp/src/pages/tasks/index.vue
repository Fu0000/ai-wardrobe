<script setup lang="ts">
import { storeToRefs } from "pinia";

import ProgressTrack from "@/components/ProgressTrack.vue";
import StateCard from "@/components/StateCard.vue";
import { useJobPolling } from "@/composables/useJobPolling";
import { presentJobStage } from "@/lib/job-progress";
import { type Job } from "@/services/jobs";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";
import { useOptimizationStore } from "@/stores/optimizations";
import { useShareStore } from "@/stores/shares";

const jobs = useJobStore();
const diagnoses = useDiagnosisStore();
const optimizations = useOptimizationStore();
const shares = useShareStore();
const { orderedJobs, refreshing } = storeToRefs(jobs);

const taskTypeLabel = (taskType: Job["task_type"]): string => {
  const labels: Record<Job["task_type"], string> = {
    STYLE_DIAGNOSIS: "穿搭诊断",
    STYLE_OPTIMIZATION: "最小改变优化",
    SHARE_ASSET: "分享卡片",
    DELETION: "隐私删除",
  };
  return labels[taskType];
};

useJobPolling({
  canStart: () => jobs.trackedJobIds.length > 0,
  poll: async () => {
    await jobs.refresh();
    return jobs.orderedJobs
      .map((job) => `${job.id}:${job.status}`)
      .join("|");
  },
  evaluate: (progressKey) => ({
    continuePolling: jobs.hasPendingJobs,
    progressKey,
  }),
});

const openJob = (job: Job) => {
  if (job.task_type === "STYLE_DIAGNOSIS") {
    const diagnosisId = diagnoses.diagnosisIdForJob(job.id);
    if (!diagnosisId) {
      uni.showToast({ title: "请从今日页恢复这项任务", icon: "none" });
      return;
    }
    const page =
      job.status === "COMPLETED"
        ? "pages/diagnosis/result"
        : "pages/diagnosis/index";
    uni.navigateTo({ url: `/${page}?id=${diagnosisId}` });
    return;
  }

  if (job.task_type === "STYLE_OPTIMIZATION") {
    const optimizationId = optimizations.optimizationIdForJob(job.id);
    if (!optimizationId) {
      uni.showToast({ title: "请从诊断结果页恢复这项任务", icon: "none" });
      return;
    }
    const page =
      job.status === "COMPLETED"
        ? "pages/optimization/result"
        : "pages/optimization/index";
    uni.navigateTo({ url: `/${page}?id=${optimizationId}` });
    return;
  }

  if (job.task_type === "SHARE_ASSET") {
    const sceneCode = shares.sceneCodeForJob(job.id);
    if (!sceneCode) {
      uni.showToast({ title: "请从优化结果页恢复这项任务", icon: "none" });
      return;
    }
    uni.navigateTo({
      url: `/pages/share/confirm?scene=${encodeURIComponent(sceneCode)}`,
    });
    return;
  }

  uni.showToast({ title: "任务详情正在接入", icon: "none" });
};

</script>

<template>
  <view class="tasks-page">
    <view class="heading">
      <text class="heading__eyebrow">BACKGROUND WORK</text>
      <text class="heading__title">任务中心</text>
      <text class="heading__copy">离开页面不会打断生成，下次打开会从这里继续。</text>
    </view>

    <view v-if="orderedJobs.length" class="task-list">
      <button
        v-for="job in orderedJobs"
        :key="job.id"
        class="task-card"
        @click="openJob(job)"
      >
        <view class="task-card__top">
          <text class="task-card__type">{{ taskTypeLabel(job.task_type) }}</text>
          <text class="task-card__progress">{{ job.progress }}%</text>
        </view>
        <text class="task-card__label">{{
          job.user_message || presentJobStage(job.status).label
        }}</text>
        <ProgressTrack
          :value="job.progress"
          :label="`${taskTypeLabel(job.task_type)}进度 ${job.progress}%`"
          size="compact"
        />
      </button>
    </view>

    <StateCard
      v-else
      kind="empty"
      density="spacious"
      spacing="section"
      :mark="refreshing ? '…' : '空'"
      title="还没有后台任务"
      message="创建诊断后，即使离开页面也会在这里继续。"
    />
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.tasks-page {
  min-height: 100vh;
  padding: 56rpx 32rpx 80rpx;
  background: $color-paper;
}

.heading {
  &__eyebrow {
    display: block;
    color: $color-vermilion;
    font-size: 20rpx;
    font-weight: 700;
    letter-spacing: 4rpx;
  }

  &__title {
    display: block;
    margin-top: 18rpx;
    font-family:
      "Songti SC",
      serif;
    font-size: 64rpx;
    font-weight: 700;
  }

  &__copy {
    display: block;
    max-width: 560rpx;
    margin-top: 18rpx;
    color: $color-muted;
    font-size: 24rpx;
    line-height: 1.7;
  }
}

.task-list {
  display: grid;
  gap: 24rpx;
  margin-top: 60rpx;
}

.task-card {
  width: 100%;
  margin: 0;
  padding: 32rpx;
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-medium;
  background: $color-white;
  box-shadow: 0 16rpx 42rpx rgba(40, 37, 31, 0.08);
  text-align: left;
  line-height: 1.4;

  &__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  &__type {
    color: $color-sage-deep;
    font-size: 20rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__progress {
    color: $color-vermilion;
    font-family:
      "Songti SC",
      serif;
    font-size: 30rpx;
    font-weight: 700;
  }

  &__label {
    display: block;
    margin-top: 24rpx;
    color: $color-ink;
    font-size: 28rpx;
  }
}

</style>
