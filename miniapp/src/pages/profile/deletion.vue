<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";

import { useJobPolling } from "@/composables/useJobPolling";
import { useDeletionStore } from "@/stores/deletion";

const deletionStore = useDeletionStore();
const {
  active,
  current,
  errorMessage,
  inferredCompleted,
  refreshing,
  submitting,
} = storeToRefs(deletionStore);

const completed = computed(
  () => current.value?.status === "COMPLETED" || inferredCompleted.value,
);
const failedFinal = computed(
  () => current.value?.status === "FAILED_FINAL",
);
const progress = computed(() => {
  if (completed.value) {
    return 100;
  }
  if (current.value?.status === "PROCESSING") {
    return 58;
  }
  if (current.value?.status === "FAILED_RETRYABLE") {
    return 42;
  }
  return 16;
});

const poller = useJobPolling({
  canStart: () => active.value,
  poll: () => deletionStore.refresh(),
  evaluate: (result) => ({
    continuePolling:
      active.value &&
      !completed.value &&
      result?.status !== "FAILED_FINAL",
    progressKey: completed.value ? "COMPLETED" : result?.status,
  }),
});

const submitDeletion = async (forceNew = false) => {
  const result = await deletionStore.request(forceNew);
  if (!result) {
    return;
  }
  poller.start();
};

const confirmDeletion = () => {
  uni.showModal({
    title: "永久删除账户数据？",
    content:
      "将删除穿搭原图、诊断、优化结果、分享卡片、投票关联和账户资料。操作开始后不能撤销。",
    confirmText: "继续确认",
    confirmColor: "#D95137",
    success(first) {
      if (!first.confirm) {
        return;
      }
      uni.showModal({
        title: "最后一次确认",
        content: "删除期间账户将被冻结；完成后需要重新登录并从空账户开始。",
        confirmText: "删除所有数据",
        confirmColor: "#D95137",
        success(second) {
          if (second.confirm) {
            void submitDeletion(false);
          }
        },
      });
    },
  });
};

const startFresh = () => {
  uni.reLaunch({ url: "/pages/index/index" });
};

</script>

<template>
  <view class="deletion-page">
    <header class="heading">
      <text class="heading__eyebrow">DATA CONTROL</text>
      <text class="heading__title">你的数据，{{ "\n" }}退出也要有结果。</text>
      <text class="heading__copy">
        删除由后台任务执行；即使关闭页面，也会保留状态并安全重试。
      </text>
    </header>

    <section v-if="!active && !current && !inferredCompleted" class="request-card">
      <text class="request-card__title">永久删除账户与数据</text>
      <view class="deletion-list">
        <view v-for="item in ['原始穿搭照片', '诊断与优化结果', '分享卡片与归因', '昵称、授权与微信身份映射']" :key="item">
          <text>×</text>
          <text>{{ item }}</text>
        </view>
      </view>
      <text class="request-card__notice">
        为保证对象存储与数据库都完成清理，删除不是同步按钮，但状态会一直可查询。
      </text>
      <button
        class="danger-action"
        :disabled="submitting"
        @click="confirmDeletion"
      >
        {{ submitting ? "正在提交" : "申请删除所有数据" }}
      </button>
    </section>

    <section v-else-if="!completed" class="status-card" aria-live="polite">
      <view class="status-card__top">
        <view>
          <text class="status-card__eyebrow">DELETION JOB</text>
          <text class="status-card__title">
            {{ failedFinal ? "需要手动重试" : "正在安全删除" }}
          </text>
        </view>
        <text class="status-card__value">{{ progress }}%</text>
      </view>
      <view class="progress-track">
        <view class="progress-track__fill" :style="{ width: `${progress}%` }" />
      </view>
      <text class="status-card__copy">
        {{ current?.user_message || "删除请求已保留，正在读取状态。" }}
      </text>
      <view class="completed-steps">
        <view
          v-for="step in [
            ['COS_OBJECTS_DELETED', '照片与派生图片'],
            ['BUSINESS_DATA_PURGED', '诊断、优化、分享和投票'],
            ['IDENTITY_REMOVED', '账户资料与身份映射'],
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
        @click="submitDeletion(true)"
      >
        重新执行删除
      </button>
      <button
        v-else
        class="refresh-action"
        :disabled="refreshing"
        @click="poller.refreshNow"
      >
        {{ refreshing ? "正在刷新" : "刷新状态" }}
      </button>
    </section>

    <section v-else class="completed-card">
      <text class="completed-card__mark">✓</text>
      <text class="completed-card__title">账户数据已删除</text>
      <text class="completed-card__copy">
        本机保存的任务、图片草稿和访问令牌也已清理。你可以离开，或从一个空账户重新开始。
      </text>
      <button class="fresh-action" @click="startFresh">从空账户开始</button>
    </section>

    <text v-if="errorMessage" class="error-copy">{{ errorMessage }}</text>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.deletion-page {
  min-height: 100vh;
  padding: 54rpx 32rpx 88rpx;
  background:
    radial-gradient(circle at 92% 0%, rgba($color-vermilion, 0.14), transparent 30%),
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
    color: $color-vermilion;
    font-size: 19rpx;
    font-weight: 700;
    letter-spacing: 3rpx;
  }

  &__title {
    margin-top: 20rpx;
    font-family: "Songti SC", serif;
    font-size: 56rpx;
    font-weight: 700;
    line-height: 1.25;
  }

  &__copy {
    margin-top: 18rpx;
    color: $color-muted;
    font-size: 22rpx;
    line-height: 1.7;
  }
}

.request-card,
.status-card,
.completed-card {
  margin-top: 42rpx;
  padding: 32rpx;
  border: 1rpx solid rgba($color-ink, 0.09);
  border-radius: $radius-large;
  background: $color-white;
  box-shadow: $shadow-soft;
}

.request-card {
  &__title,
  &__notice {
    display: block;
  }

  &__title {
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__notice {
    margin-top: 26rpx;
    padding: 22rpx;
    border-radius: $radius-medium;
    background: $color-paper-deep;
    color: $color-muted;
    font-size: 20rpx;
    line-height: 1.65;
  }
}

.deletion-list {
  display: grid;
  gap: 18rpx;
  margin-top: 28rpx;

  view {
    display: flex;
    gap: 14rpx;
    color: $color-muted;
    font-size: 22rpx;

    text:first-child {
      color: $color-vermilion;
      font-weight: 700;
    }
  }
}

.danger-action,
.fresh-action {
  height: 96rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  background: $color-vermilion;
  color: $color-white;
  font-size: 24rpx;
  font-weight: 700;
  line-height: 96rpx;
}

.status-card {
  &__top {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
  }

  &__eyebrow,
  &__title,
  &__copy {
    display: block;
  }

  &__eyebrow {
    color: $color-sage-deep;
    font-size: 18rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__title {
    margin-top: 10rpx;
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__value {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 52rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 24rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.65;
  }
}

.progress-track {
  height: 9rpx;
  margin-top: 28rpx;
  overflow: hidden;
  border-radius: 999rpx;
  background: $color-paper-deep;

  &__fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, $color-sage, $color-vermilion);
  }
}

.completed-steps {
  display: grid;
  gap: 16rpx;
  margin-top: 28rpx;

  view {
    display: flex;
    gap: 14rpx;
    color: rgba($color-ink, 0.38);
    font-size: 21rpx;

    &.is-done {
      color: $color-sage-deep;
    }
  }
}

.refresh-action {
  height: 88rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  background: $color-ink;
  color: $color-white;
  line-height: 88rpx;
}

.completed-card {
  display: grid;
  justify-items: center;
  padding-block: 60rpx;
  text-align: center;

  &__mark {
    display: grid;
    place-items: center;
    width: 84rpx;
    height: 84rpx;
    border-radius: 50%;
    background: rgba($color-sage, 0.18);
    color: $color-sage-deep;
    font-size: 34rpx;
  }

  &__title {
    margin-top: 24rpx;
    font-family: "Songti SC", serif;
    font-size: 36rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 16rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.7;
  }
}

.error-copy {
  display: block;
  margin-top: 24rpx;
  color: $color-vermilion;
  font-size: 21rpx;
  text-align: center;
}
</style>
