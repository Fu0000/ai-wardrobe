<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed } from "vue";

import ProgressTrack from "@/components/ProgressTrack.vue";
import { useJobPolling } from "@/composables/useJobPolling";
import { useAssetStore } from "@/stores/assets";
import { usePhotoDeletionStore } from "@/stores/photo-deletion";

const assets = useAssetStore();
const deletions = usePhotoDeletionStore();
const { active, current, errorMessage, refreshing, submitting } =
  storeToRefs(deletions);

const hasPhoto = computed(
  () => Boolean(assets.assetId || deletions.assetId),
);
const completed = computed(() => current.value?.status === "COMPLETED");
const failedFinal = computed(() => current.value?.status === "FAILED_FINAL");
const progress = computed(() => {
  if (completed.value) {
    return 100;
  }
  if (current.value?.status === "PROCESSING") {
    return 62;
  }
  if (current.value?.status === "FAILED_RETRYABLE") {
    return 44;
  }
  return 18;
});

const poller = useJobPolling({
  canStart: () => active.value,
  poll: () => deletions.refresh(),
  evaluate: (result) => ({
    continuePolling:
      active.value &&
      result?.status !== "COMPLETED" &&
      result?.status !== "FAILED_FINAL",
    progressKey: result?.status,
  }),
});

const submit = async (forceNew = false) => {
  const result = await deletions.request(forceNew);
  if (!result) {
    return;
  }
  poller.start();
};

const confirmDeletion = () => {
  uni.showModal({
    title: "删除这张照片？",
    content:
      "原图、关联诊断、优化图和分享卡片会一并删除，账户和个人设置不受影响。此操作不能撤销。",
    confirmText: "永久删除",
    confirmColor: "#D95137",
    success(result) {
      if (result.confirm) {
        void submit(false);
      }
    },
  });
};

const finish = () => {
  deletions.clear();
  uni.reLaunch({ url: "/pages/index/index" });
};

</script>

<template>
  <view class="photo-page">
    <header class="heading">
      <text class="heading__eyebrow">PHOTO CONTROL</text>
      <text class="heading__title">删掉一张照片，{{ "\n" }}也要清理它的所有去向。</text>
      <text class="heading__copy">
        删除任务会覆盖对象存储、诊断、优化结果和公开分享关系，可关闭页面后继续执行。
      </text>
    </header>

    <section v-if="completed" class="card completed-card">
      <text class="completed-card__mark">✓</text>
      <text class="card__title">照片数据已删除</text>
      <text class="card__copy">
        原图及关联的 AI 派生图片已不可访问，本机保存的照片草稿也已清理。
      </text>
      <button class="primary-action" @click="finish">返回首页</button>
    </section>

    <section v-else-if="active || current" class="card status-card" aria-live="polite">
      <view class="status-card__top">
        <view>
          <text class="status-card__eyebrow">DELETION JOB</text>
          <text class="card__title">
            {{ failedFinal ? "需要手动重试" : "正在清理照片数据" }}
          </text>
        </view>
        <text class="status-card__value">{{ progress }}%</text>
      </view>
      <ProgressTrack
        :value="progress"
        label="照片删除进度"
        size="prominent"
        tone="contrast"
      />
      <text class="card__copy">
        {{ current?.user_message || "删除请求已保留，正在读取状态。" }}
      </text>
      <view class="step-list">
        <view
          v-for="step in [
            ['COS_OBJECTS_DELETED', '原图与派生图片'],
            ['BUSINESS_DATA_PURGED', '诊断、优化与分享关系'],
            ['OBJECT_ACCESS_REVOKED', '已有访问地址失效'],
          ]"
          :key="step[0]"
          :class="{ 'is-done': current?.completed_steps.includes(step[0]) }"
        >
          <text>{{ current?.completed_steps.includes(step[0]) ? "✓" : "·" }}</text>
          <text>{{ step[1] }}</text>
        </view>
      </view>
      <button
        v-if="failedFinal"
        class="danger-action"
        :disabled="submitting"
        @click="submit(true)"
      >
        重新执行删除
      </button>
      <button
        v-else
        class="secondary-action"
        :disabled="refreshing"
        @click="poller.refreshNow"
      >
        {{ refreshing ? "正在刷新" : "刷新状态" }}
      </button>
    </section>

    <section v-else-if="hasPhoto" class="card request-card">
      <image
        v-if="assets.image?.localPath"
        class="photo-preview"
        :src="assets.image.localPath"
        mode="aspectFill"
      />
      <text class="card__title">当前穿搭照片</text>
      <text class="card__copy">
        删除会同步撤销相关分享，好友将无法再打开对应卡片；账户资料与免费次数记录会保留。
      </text>
      <button
        class="danger-action"
        :disabled="submitting"
        @click="confirmDeletion"
      >
        {{ submitting ? "正在提交" : "永久删除照片与派生结果" }}
      </button>
    </section>

    <section v-else class="card empty-card">
      <text class="empty-card__mark">0</text>
      <text class="card__title">当前没有照片</text>
      <text class="card__copy">上传并完成照片后，可以在这里单独管理和删除。</text>
      <button class="secondary-action" @click="finish">返回首页</button>
    </section>

    <text v-if="errorMessage" class="error-copy">{{ errorMessage }}</text>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.photo-page {
  min-height: 100vh;
  padding: 54rpx 32rpx 88rpx;
  background:
    radial-gradient(circle at 90% 0%, rgba($color-sage, 0.2), transparent 30%),
    $color-paper;
  color: $color-ink;
}

.heading {
  &__eyebrow,
  &__title,
  &__copy {
    display: block;
  }

  &__eyebrow {
    color: $color-sage-deep;
    font-size: 19rpx;
    font-weight: 700;
    letter-spacing: 3rpx;
  }

  &__title {
    margin-top: 20rpx;
    font-family: "Songti SC", serif;
    font-size: 52rpx;
    font-weight: 700;
    line-height: 1.28;
  }

  &__copy {
    margin-top: 18rpx;
    color: $color-muted;
    font-size: 22rpx;
    line-height: 1.68;
  }
}

.card {
  margin-top: 40rpx;
  padding: 32rpx;
  border: 1rpx solid rgba($color-ink, 0.09);
  border-radius: $radius-large;
  background: $color-white;
  box-shadow: $shadow-soft;

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    font-family: "Songti SC", serif;
    font-size: 32rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 14rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.65;
  }
}

.photo-preview {
  width: 100%;
  height: 360rpx;
  margin-bottom: 28rpx;
  border-radius: $radius-medium;
}

.status-card {
  &__top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
  }

  &__eyebrow {
    display: block;
    margin-bottom: 8rpx;
    color: $color-vermilion;
    font-size: 17rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__value {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 48rpx;
    font-weight: 700;
  }
}

.step-list {
  display: grid;
  gap: 16rpx;
  margin-top: 28rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid rgba($color-ink, 0.09);

  view {
    display: flex;
    gap: 14rpx;
    color: rgba($color-muted, 0.62);
    font-size: 21rpx;
  }

  .is-done {
    color: $color-sage-deep;
  }
}

.danger-action,
.primary-action,
.secondary-action {
  width: 100%;
  height: 94rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  font-size: 23rpx;
  font-weight: 700;
  line-height: 94rpx;
}

.danger-action {
  background: $color-vermilion;
  color: $color-white;
}

.primary-action {
  background: $color-ink;
  color: $color-white;
}

.secondary-action {
  border: 1rpx solid rgba($color-ink, 0.17);
  background: transparent;
  color: $color-ink;
}

.completed-card,
.empty-card {
  text-align: center;

  &__mark {
    display: block;
    margin-bottom: 18rpx;
    color: $color-sage-deep;
    font-family: "Songti SC", serif;
    font-size: 72rpx;
    font-weight: 700;
  }
}

.error-copy {
  display: block;
  margin-top: 22rpx;
  padding: 20rpx;
  border-radius: $radius-small;
  background: rgba($color-vermilion, 0.09);
  color: $color-vermilion;
  font-size: 21rpx;
  line-height: 1.55;
}
</style>
