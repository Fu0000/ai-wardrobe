<script setup lang="ts">
import { onLoad, onShow } from "@dcloudio/uni-app";
import { computed } from "vue";
import { storeToRefs } from "pinia";

import { type VoteChoice } from "@/services/shares";
import { useShareStore } from "@/stores/shares";

const shares = useShareStore();
const { current, errorMessage, refreshing, voting } = storeToRefs(shares);
let sceneCode: string | null = null;

const share = computed(() =>
  current.value?.scene_code === sceneCode ? current.value : null,
);
const voteTotal = computed(
  () => (share.value?.votes.before ?? 0) + (share.value?.votes.after ?? 0),
);

const refresh = () => {
  if (sceneCode) {
    void shares.refresh(sceneCode);
  }
};

const vote = (choice: VoteChoice) => {
  void shares.vote(choice);
};

const continueExperience = async () => {
  if (sceneCode) {
    uni.setStorageSync("aiw:attribution:v1", {
      source: "SHARE_SCENE",
      sceneCode,
      capturedAt: Date.now(),
    });
    await shares.recordContinue(sceneCode);
  }
  uni.reLaunch({ url: "/pages/index/index" });
};

onLoad((query) => {
  sceneCode = typeof query?.scene === "string" ? query.scene : null;
});
onShow(refresh);
</script>

<template>
  <view class="landing-page">
    <header class="heading">
      <text class="heading__eyebrow">A SMALL CHANGE</text>
      <text class="heading__title">你更喜欢哪一个？</text>
      <text class="heading__copy">这是 AI 辅助编辑的穿搭对比，人物与未修改部分应保持一致。</text>
    </header>

    <view v-if="refreshing && !share" class="state-card">正在打开分享…</view>
    <view v-else-if="errorMessage && !share" class="state-card state-card--error">
      <text>{{ errorMessage }}</text>
      <button @click="refresh">重新加载</button>
    </view>

    <main v-else-if="share?.status === 'ACTIVE' && share.card_url">
      <section class="card-stage">
        <image class="card-stage__image" :src="share.card_url" mode="widthFix" />
        <view class="card-stage__meta">
          <text>AI EDITED</text>
          <text>LEVEL {{ share.change_level }}</text>
          <text v-if="share.score !== null">SCORE {{ share.score }}</text>
        </view>
      </section>

      <section class="changes">
        <text class="changes__eyebrow">WHAT CHANGED</text>
        <view
          v-for="change in share.changes"
          :key="change.priority"
          class="change-item"
        >
          <text class="change-item__number">0{{ change.priority }}</text>
          <view>
            <text class="change-item__title">{{ change.instruction }}</text>
            <text class="change-item__copy">{{ change.reason }}</text>
          </view>
        </view>
      </section>

      <section class="vote-card">
        <text class="vote-card__title">投下你的一票</text>
        <text class="vote-card__copy">
          {{ voteTotal ? `${voteTotal} 人已参与` : "成为第一个投票的人" }}
        </text>
        <view class="vote-grid">
          <button
            :class="{ 'is-selected': share.viewer_choice === 'BEFORE' }"
            :disabled="voting"
            @click="vote('BEFORE')"
          >
            <text>BEFORE</text>
            <text>{{ share.votes.before }}</text>
          </button>
          <button
            :class="{ 'is-selected': share.viewer_choice === 'AFTER' }"
            :disabled="voting"
            @click="vote('AFTER')"
          >
            <text>AFTER</text>
            <text>{{ share.votes.after }}</text>
          </button>
        </view>
        <text v-if="share.viewer_choice" class="vote-card__thanks">
          已记录；你也可以重新选择。
        </text>
      </section>

      <button class="continue-action" @click="continueExperience">
        开始我的穿搭诊断
      </button>
      <text class="privacy-copy">你看到的是独立分享卡片，无法访问分享者的原始照片或账号资料。</text>
    </main>

    <view v-else-if="share" class="state-card">
      <text>{{ share.user_message || "分享卡片仍在准备中。" }}</text>
      <button @click="refresh">刷新状态</button>
    </view>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.landing-page {
  min-height: 100vh;
  padding: 52rpx 32rpx 88rpx;
  background: $color-ink;
  color: $color-white;
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
    margin-top: 18rpx;
    font-family: "Songti SC", serif;
    font-size: 56rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 16rpx;
    color: rgba($color-white, 0.62);
    font-size: 21rpx;
    line-height: 1.65;
  }
}

.state-card {
  display: grid;
  gap: 20rpx;
  margin-top: 42rpx;
  padding: 60rpx 32rpx;
  border: 1rpx solid rgba($color-white, 0.14);
  border-radius: $radius-large;
  color: rgba($color-white, 0.7);
  text-align: center;

  button {
    height: 84rpx;
    border-radius: 999rpx;
    background: $color-vermilion;
    color: $color-white;
    line-height: 84rpx;
  }
}

.card-stage {
  margin-top: 36rpx;
  overflow: hidden;
  border-radius: $radius-large;
  background: $color-paper;

  &__image {
    display: block;
    width: 100%;
  }

  &__meta {
    display: flex;
    gap: 12rpx;
    padding: 18rpx 22rpx 22rpx;

    text {
      padding: 8rpx 12rpx;
      border-radius: 999rpx;
      background: rgba($color-sage-deep, 0.1);
      color: $color-sage-deep;
      font-size: 16rpx;
      font-weight: 700;
    }
  }
}

.changes {
  margin-top: 26rpx;
  padding: 30rpx;
  border-radius: $radius-large;
  background: $color-paper;
  color: $color-ink;

  &__eyebrow {
    color: $color-sage-deep;
    font-size: 18rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }
}

.change-item {
  display: grid;
  grid-template-columns: 48rpx 1fr;
  gap: 16rpx;
  margin-top: 22rpx;
  padding-top: 22rpx;
  border-top: 1rpx solid rgba($color-ink, 0.1);

  &__number {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 24rpx;
    font-weight: 700;
  }

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    font-size: 23rpx;
    font-weight: 700;
    line-height: 1.5;
  }

  &__copy {
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 20rpx;
    line-height: 1.6;
  }
}

.vote-card {
  margin-top: 26rpx;
  padding: 34rpx 28rpx;
  border: 1rpx solid rgba($color-white, 0.14);
  border-radius: $radius-large;
  text-align: center;

  &__title,
  &__copy,
  &__thanks {
    display: block;
  }

  &__title {
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__copy,
  &__thanks {
    margin-top: 10rpx;
    color: rgba($color-white, 0.58);
    font-size: 19rpx;
  }
}

.vote-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16rpx;
  margin-top: 28rpx;

  button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 92rpx;
    padding: 0 24rpx;
    border: 1rpx solid rgba($color-white, 0.2);
    border-radius: $radius-medium;
    background: transparent;
    color: $color-white;
    font-size: 22rpx;
    line-height: 92rpx;

    &.is-selected {
      border-color: $color-vermilion;
      background: rgba($color-vermilion, 0.18);
    }
  }
}

.continue-action {
  height: 98rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  background: $color-vermilion;
  color: $color-white;
  font-size: 24rpx;
  font-weight: 700;
  line-height: 98rpx;
}

.privacy-copy {
  display: block;
  margin-top: 18rpx;
  color: rgba($color-white, 0.46);
  font-size: 18rpx;
  line-height: 1.6;
  text-align: center;
}
</style>
