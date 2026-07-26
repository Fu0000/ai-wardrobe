<script setup lang="ts">
import { onLoad, onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";

import { useOptimizationStore } from "@/stores/optimizations";

const optimizations = useOptimizationStore();
const { current, errorMessage, refreshing } = storeToRefs(optimizations);
const comparePosition = ref(50);
let optimizationId: string | null = null;

const result = computed(() =>
  current.value?.id === optimizationId ? current.value : null,
);
const levelCopy = computed(() => {
  const copies = {
    1: "只调整穿法",
    2: "替换一件",
    3: "最多替换两件",
  };
  return result.value ? copies[result.value.change_level] : "";
});

const updateCompare = (event: unknown) => {
  const change = event as { detail?: { value?: number } };
  comparePosition.value = Number(change.detail?.value ?? 50);
};

const refresh = () => {
  if (optimizationId) {
    void optimizations.refresh(optimizationId);
  }
};

const openProgress = () => {
  if (optimizationId) {
    uni.redirectTo({ url: `/pages/optimization/index?id=${optimizationId}` });
  }
};

const saveAfter = () => {
  if (!result.value?.after_image_url) {
    return;
  }
  uni.downloadFile({
    url: result.value.after_image_url,
    success(download) {
      if (download.statusCode !== 200) {
        uni.showToast({ title: "图片下载失败", icon: "none" });
        return;
      }
      uni.saveImageToPhotosAlbum({
        filePath: download.tempFilePath,
        success() {
          uni.showToast({ title: "已保存到相册", icon: "success" });
        },
        fail() {
          uni.showToast({ title: "需要相册保存权限", icon: "none" });
        },
      });
    },
    fail() {
      uni.showToast({ title: "网络异常，请稍后重试", icon: "none" });
    },
  });
};

const openSharePreview = () => {
  if (!result.value) {
    return;
  }
  uni.navigateTo({
    url: `/pages/share/confirm?optimization=${result.value.id}`,
  });
};

onLoad((query) => {
  optimizationId =
    (typeof query?.id === "string" ? query.id : null) ??
    optimizations.recentOptimizationId;
});
onShow(refresh);
</script>

<template>
  <view class="compare-page">
    <header class="heading">
      <view>
        <text class="heading__eyebrow">BEFORE / AFTER</text>
        <text class="heading__title">改变很少，{{ "\n" }}但方向更清楚。</text>
      </view>
      <view v-if="result" class="quality-badge">
        <text>✓</text>
        <text>一致性检查通过</text>
      </view>
    </header>

    <view v-if="refreshing && !result" class="state-card">正在恢复优化结果…</view>
    <view v-else-if="errorMessage && !result" class="state-card state-card--error">
      <text>{{ errorMessage }}</text>
      <button @click="refresh">重新加载</button>
    </view>
    <view
      v-else-if="result && result.job_status !== 'COMPLETED'"
      class="state-card"
    >
      <text>这项优化仍在后台处理中。</text>
      <button @click="openProgress">查看生成进度</button>
    </view>
    <view
      v-else-if="
        result &&
        (!result.before_image_url ||
          !result.after_image_url ||
          !result.quality_passed)
      "
      class="state-card state-card--error"
    >
      <text>这次结果没有通过一致性检查，不会向你展示。</text>
      <button @click="openProgress">返回任务详情</button>
    </view>

    <main
      v-else-if="
        result?.before_image_url &&
        result.after_image_url &&
        result.quality_passed
      "
    >
      <section class="compare-stage">
        <image
          class="compare-image compare-image--after"
          :src="result.after_image_url"
          mode="aspectFill"
        />
        <view
          class="compare-stage__before"
          :style="{ width: `${comparePosition}%` }"
        >
          <image
            class="compare-image compare-image--before"
            :src="result.before_image_url"
            mode="aspectFill"
          />
        </view>
        <view
          class="compare-stage__divider"
          :style="{ left: `${comparePosition}%` }"
        >
          <text>↔</text>
        </view>
        <text class="compare-stage__label compare-stage__label--before">BEFORE</text>
        <text class="compare-stage__label compare-stage__label--after">AFTER</text>
      </section>

      <view class="compare-controls">
        <button
          :aria-pressed="comparePosition === 100"
          @click="comparePosition = 100"
        >
          看原图
        </button>
        <slider
          class="compare-slider"
          :value="comparePosition"
          min="0"
          max="100"
          active-color="#D95137"
          background-color="#4A4B47"
          block-color="#FFFDF8"
          block-size="20"
          aria-label="拖动比较优化前后"
          @changing="updateCompare"
          @change="updateCompare"
        />
        <button
          :aria-pressed="comparePosition === 0"
          @click="comparePosition = 0"
        >
          看优化
        </button>
      </view>

      <section class="change-card">
        <view class="change-card__heading">
          <view>
            <text class="change-card__eyebrow">CHANGE BUDGET</text>
            <text class="change-card__title">Level {{ result.change_level }} · {{ levelCopy }}</text>
          </view>
          <text class="change-card__count">{{ result.changes.length }}</text>
        </view>

        <view class="change-list">
          <article
            v-for="change in result.changes"
            :key="change.priority"
            class="change-item"
          >
            <text class="change-item__number">0{{ change.priority }}</text>
            <view>
              <text class="change-item__instruction">{{ change.instruction }}</text>
              <text class="change-item__reason">{{ change.reason }}</text>
              <text class="change-item__preserve">保留：{{ change.preserves }}</text>
            </view>
          </article>
        </view>
      </section>

      <view class="result-actions">
        <button class="result-actions__save" @click="saveAfter">保存 After</button>
        <button class="result-actions__share" @click="openSharePreview">生成分享卡片</button>
      </view>
      <text class="privacy-copy">
        当前图片仍为私有结果；分享时将生成不含私有访问地址的独立资产。
      </text>
    </main>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.compare-page {
  min-height: 100vh;
  padding: 48rpx 32rpx 88rpx;
  background: $color-ink;
  color: $color-white;
}

.heading {
  display: flex;
  gap: 24rpx;
  align-items: flex-end;
  justify-content: space-between;

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
    font-size: 50rpx;
    font-weight: 700;
    line-height: 1.24;
  }
}

.quality-badge {
  display: grid;
  flex: 0 0 120rpx;
  gap: 8rpx;
  justify-items: center;
  padding: 18rpx 12rpx;
  border: 1rpx solid rgba($color-sage, 0.42);
  border-radius: $radius-medium;
  color: $color-sage;
  font-size: 17rpx;
  line-height: 1.3;
  text-align: center;

  text:first-child {
    font-size: 28rpx;
  }
}

.state-card {
  display: grid;
  gap: 20rpx;
  margin-top: 48rpx;
  padding: 60rpx 30rpx;
  border: 1rpx solid rgba($color-white, 0.14);
  border-radius: $radius-large;
  color: rgba($color-white, 0.7);
  font-size: 23rpx;
  text-align: center;

  button {
    height: 88rpx;
    border-radius: 999rpx;
    background: $color-vermilion;
    color: $color-white;
    line-height: 88rpx;
  }
}

.compare-stage {
  position: relative;
  width: 686rpx;
  height: 910rpx;
  margin-top: 44rpx;
  overflow: hidden;
  border-radius: $radius-large;
  background: #2a2b28;

  &__before {
    position: absolute;
    inset: 0 auto 0 0;
    overflow: hidden;
  }

  &__divider {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 3rpx;
    background: rgba($color-white, 0.9);
    transform: translateX(-50%);

    text {
      position: absolute;
      top: 50%;
      left: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 70rpx;
      height: 70rpx;
      border-radius: 50%;
      background: $color-white;
      color: $color-ink;
      font-size: 28rpx;
      transform: translate(-50%, -50%);
    }
  }

  &__label {
    position: absolute;
    top: 22rpx;
    padding: 10rpx 14rpx;
    border-radius: 999rpx;
    background: rgba($color-ink, 0.66);
    font-size: 16rpx;
    font-weight: 700;
    letter-spacing: 2rpx;

    &--before {
      left: 20rpx;
    }

    &--after {
      right: 20rpx;
    }
  }
}

.compare-image {
  position: absolute;
  inset: 0;
  width: 686rpx;
  height: 910rpx;
}

.compare-controls {
  display: grid;
  grid-template-columns: 110rpx 1fr 110rpx;
  gap: 12rpx;
  align-items: center;
  margin-top: 20rpx;

  button {
    min-height: 72rpx;
    margin: 0;
    padding: 0;
    background: transparent;
    color: rgba($color-white, 0.72);
    font-size: 19rpx;
    line-height: 72rpx;
  }
}

.compare-slider {
  width: 100%;
  margin: 0;
}

.change-card {
  margin-top: 34rpx;
  padding: 32rpx 28rpx;
  border-radius: $radius-large;
  background: $color-paper;
  color: $color-ink;

  &__heading {
    display: flex;
    justify-content: space-between;
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
    margin-top: 8rpx;
    font-family: "Songti SC", serif;
    font-size: 30rpx;
    font-weight: 700;
  }

  &__count {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 58rpx;
    font-weight: 700;
  }
}

.change-list {
  display: grid;
  gap: 24rpx;
  margin-top: 30rpx;
}

.change-item {
  display: grid;
  grid-template-columns: 50rpx 1fr;
  gap: 16rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid rgba($color-ink, 0.1);

  &__number {
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 25rpx;
    font-weight: 700;
  }

  &__instruction,
  &__reason,
  &__preserve {
    display: block;
  }

  &__instruction {
    font-size: 24rpx;
    font-weight: 700;
    line-height: 1.5;
  }

  &__reason {
    margin-top: 10rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.6;
  }

  &__preserve {
    margin-top: 9rpx;
    color: $color-sage-deep;
    font-size: 19rpx;
  }
}

.result-actions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16rpx;
  margin-top: 28rpx;

  button {
    height: 96rpx;
    margin: 0;
    border-radius: 999rpx;
    font-size: 23rpx;
    font-weight: 700;
    line-height: 96rpx;
  }

  &__save {
    background: $color-white;
    color: $color-ink;
  }

  &__share {
    background: $color-vermilion;
    color: $color-white;
  }
}

.privacy-copy {
  display: block;
  margin-top: 20rpx;
  color: rgba($color-white, 0.52);
  font-size: 18rpx;
  line-height: 1.6;
  text-align: center;
}
</style>
