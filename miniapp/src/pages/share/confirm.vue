<script setup lang="ts">
import {
  onHide,
  onLoad,
  onShareAppMessage,
  onShareTimeline,
  onShow,
  onUnload,
} from "@dcloudio/uni-app";
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";

import { nextJobPollDelay } from "@/lib/job-progress";
import {
  shareLandingPath,
  shareLandingQuery,
} from "@/lib/navigation";
import { useOptimizationStore } from "@/stores/optimizations";
import { useShareStore } from "@/stores/shares";

const optimizations = useOptimizationStore();
const shares = useShareStore();
const { current, errorMessage, refreshing, submitting } = storeToRefs(shares);
const displayScore = ref(false);
let optimizationId: string | null = null;
let sceneCode: string | null = null;
let pollTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
let pollAttempt = 0;
let polling = false;

const share = computed(() =>
  current.value?.scene_code === sceneCode ? current.value : null,
);
const isActive = computed(
  () => share.value?.status === "ACTIVE" && Boolean(share.value.card_url),
);
const isFailed = computed(
  () =>
    share.value?.status === "FAILED" ||
    share.value?.job_status === "FAILED_FINAL",
);

const stopPolling = () => {
  polling = false;
  if (pollTimer) {
    globalThis.clearTimeout(pollTimer);
    pollTimer = null;
  }
};

const scheduleRefresh = () => {
  if (!polling || !sceneCode) {
    return;
  }
  pollTimer = globalThis.setTimeout(() => {
    pollAttempt += 1;
    void refresh();
  }, nextJobPollDelay(pollAttempt));
};

const refresh = async () => {
  if (!sceneCode || !polling) {
    return;
  }
  const result = await shares.refresh(sceneCode);
  if (!polling) {
    return;
  }
  if (result?.status === "ACTIVE" || result?.status === "FAILED") {
    stopPolling();
    return;
  }
  scheduleRefresh();
};

const create = async (forceNew = false) => {
  if (!optimizationId) {
    return;
  }
  try {
    const result = await shares.create(
      optimizationId,
      displayScore.value,
      "PREVIEW",
      forceNew,
    );
    sceneCode = result.scene_code;
    polling = true;
    pollAttempt = 0;
    if (result.status === "ACTIVE") {
      stopPolling();
      return;
    }
    await refresh();
  } catch {
    // The store retains safe copy and the idempotency key for weak-network retry.
  }
};

const updateDisplayScore = (event: unknown) => {
  const change = event as { detail?: { value?: boolean } };
  displayScore.value = Boolean(change.detail?.value);
};

const cancel = () => {
  stopPolling();
  uni.navigateBack();
};

onShareAppMessage(() => {
  if (!isActive.value || !share.value?.card_url) {
    return {
      title: "AI Wardrobe · Minimal Change",
      path: "/pages/index/index",
    };
  }
  void shares.recordInvocation(share.value.scene_code, "WECHAT_FRIEND");
  return {
    title: "我只改了必要的部分，你更喜欢 Before 还是 After？",
    path: shareLandingPath(share.value.scene_code, "WECHAT_FRIEND"),
    imageUrl: share.value.card_url,
  };
});

onShareTimeline(() => {
  if (!isActive.value || !share.value?.card_url) {
    return {
      title: "AI Wardrobe · Minimal Change",
      query: "",
    };
  }
  void shares.recordInvocation(share.value.scene_code, "WECHAT_TIMELINE");
  return {
    title: "我只改了必要的部分，你更喜欢 Before 还是 After？",
    query: shareLandingQuery(
      share.value.scene_code,
      "WECHAT_TIMELINE",
    ),
    imageUrl: share.value.card_url,
  };
});

onLoad((query) => {
  optimizationId =
    (typeof query?.optimization === "string" ? query.optimization : null) ??
    optimizations.recentOptimizationId;
  sceneCode =
    (typeof query?.scene === "string" ? query.scene : null) ??
    shares.activeSceneCode;
});
onShow(() => {
  if (!sceneCode) {
    return;
  }
  polling = true;
  pollAttempt = 0;
  void refresh();
});
onHide(stopPolling);
onUnload(stopPolling);
</script>

<template>
  <view class="share-page">
    <header class="heading">
      <text class="heading__eyebrow">PRIVACY-SAFE SHARE</text>
      <text class="heading__title">分享改变，{{ "\n" }}不分享你的隐私。</text>
      <text class="heading__copy">
        系统会重新生成一张独立卡片，移除照片元数据，也不会把私有图片地址交给好友。
      </text>
    </header>

    <section v-if="!share" class="prepare-card">
      <view class="privacy-list">
        <view v-for="item in ['独立分享资产', '移除 EXIF 元数据', '标识 AI 编辑', '原图仍保持私有']" :key="item">
          <text>✓</text>
          <text>{{ item }}</text>
        </view>
      </view>
      <view class="score-option">
        <view>
          <text class="score-option__title">在卡片中展示诊断分数</text>
          <text class="score-option__copy">默认关闭；修改说明仍会展示。</text>
        </view>
        <switch
          :checked="displayScore"
          color="#D95137"
          @change="updateDisplayScore"
        />
      </view>
      <button
        class="primary-action"
        :disabled="submitting || !optimizationId"
        @click="create(false)"
      >
        {{ submitting ? "正在创建" : "生成分享卡片" }}
      </button>
      <button class="cancel-action" @click="cancel">暂不分享</button>
    </section>

    <section v-else-if="!isActive && !isFailed" class="generation-card" aria-live="polite">
      <text class="generation-card__mark">↗</text>
      <text class="generation-card__title">正在制作独立分享卡片</text>
      <text class="generation-card__copy">
        {{ share.user_message || "通常只需要几秒；离开页面也不会中断。" }}
      </text>
      <view class="progress-track">
        <view
          class="progress-track__fill"
          :style="{ width: `${share.job_status === 'QUALITY_CHECKING' ? 86 : 48}%` }"
        />
      </view>
      <button class="cancel-action" @click="cancel">先离开</button>
    </section>

    <section v-else-if="isFailed" class="generation-card generation-card--error">
      <text class="generation-card__mark">!</text>
      <text class="generation-card__title">这次卡片没有生成成功</text>
      <text class="generation-card__copy">
        {{ share?.user_message || errorMessage }}
      </text>
      <button class="primary-action" :disabled="submitting" @click="create(true)">
        重新生成
      </button>
    </section>

    <main v-else-if="share?.card_url" class="share-preview">
      <view class="share-preview__top">
        <view>
          <text class="share-preview__eyebrow">FINAL PREVIEW</text>
          <text class="share-preview__title">好友将看到这张卡片</text>
        </view>
        <text class="share-preview__badge">AI EDITED</text>
      </view>
      <image
        class="share-preview__image"
        :src="share.card_url"
        mode="widthFix"
      />
      <text class="share-preview__privacy">
        不包含用户身份、原始私有 URL、任务信息或额度信息。
      </text>
      <button class="share-action" open-type="share">分享给微信好友</button>
      <button class="cancel-action" @click="cancel">取消</button>
    </main>

    <text v-if="errorMessage && !isFailed" class="error-copy">
      {{ refreshing ? "正在重试…" : errorMessage }}
    </text>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.share-page {
  min-height: 100vh;
  padding: 52rpx 32rpx 88rpx;
  background:
    radial-gradient(circle at 90% 0%, rgba($color-sage, 0.22), transparent 34%),
    $color-paper;
  color: $color-ink;
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
    margin-top: 20rpx;
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
    line-height: 1.7;
  }
}

.prepare-card,
.generation-card,
.share-preview {
  margin-top: 42rpx;
  padding: 30rpx;
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: $color-white;
  box-shadow: 0 24rpx 60rpx rgba(40, 37, 31, 0.1);
}

.privacy-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18rpx;

  view {
    display: flex;
    gap: 12rpx;
    align-items: center;
    color: $color-sage-deep;
    font-size: 21rpx;
  }
}

.score-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 30rpx;
  padding: 26rpx 0;
  border-block: 1rpx solid rgba($color-ink, 0.08);

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    font-size: 24rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 19rpx;
  }
}

.primary-action,
.share-action {
  height: 96rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  background: $color-vermilion;
  color: $color-white;
  font-size: 24rpx;
  font-weight: 700;
  line-height: 96rpx;
}

.cancel-action {
  height: 76rpx;
  margin-top: 10rpx;
  background: transparent;
  color: $color-muted;
  font-size: 22rpx;
  line-height: 76rpx;
}

.generation-card {
  display: grid;
  justify-items: center;
  padding-block: 56rpx;
  text-align: center;

  &__mark {
    display: grid;
    place-items: center;
    width: 82rpx;
    height: 82rpx;
    border-radius: 50%;
    background: $color-paper-deep;
    color: $color-vermilion;
    font-size: 34rpx;
  }

  &__title {
    margin-top: 24rpx;
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 14rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.6;
  }

  &--error .generation-card__mark {
    background: rgba($color-vermilion, 0.12);
  }
}

.progress-track {
  width: 100%;
  height: 8rpx;
  margin-top: 32rpx;
  overflow: hidden;
  border-radius: 999rpx;
  background: $color-paper-deep;

  &__fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, $color-sage, $color-vermilion);
    transition: width 300ms ease;
  }
}

.share-preview {
  &__top {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
  }

  &__eyebrow,
  &__title,
  &__privacy {
    display: block;
  }

  &__eyebrow {
    color: $color-sage-deep;
    font-size: 18rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__title {
    margin-top: 8rpx;
    font-size: 28rpx;
    font-weight: 700;
  }

  &__badge {
    padding: 10rpx 14rpx;
    border-radius: 999rpx;
    background: $color-vermilion;
    color: $color-white;
    font-size: 16rpx;
    font-weight: 700;
  }

  &__image {
    width: 100%;
    margin-top: 24rpx;
    border-radius: $radius-medium;
  }

  &__privacy {
    margin-top: 18rpx;
    color: $color-muted;
    font-size: 19rpx;
    line-height: 1.6;
    text-align: center;
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
