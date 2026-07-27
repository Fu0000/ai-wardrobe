<script setup lang="ts">
import { onShow } from "@dcloudio/uni-app";
import { computed, ref } from "vue";
import { storeToRefs } from "pinia";

import { type Occasion } from "@/services/diagnoses";
import { useAssetStore } from "@/stores/assets";
import { useDiagnosisStore } from "@/stores/diagnoses";
import { useJobStore } from "@/stores/jobs";

const assets = useAssetStore();
const diagnoses = useDiagnosisStore();
const jobs = useJobStore();
const {
  errorMessage: assetErrorMessage,
  image,
  phase,
  progress,
} = storeToRefs(assets);
const {
  current: currentDiagnosis,
  errorCode: diagnosisErrorCode,
  errorMessage: diagnosisErrorMessage,
  recentDiagnosisId,
  submitting,
} = storeToRefs(diagnoses);
const { hasPendingJobs } = storeToRefs(jobs);
const selectedOccasion = ref<Occasion | null>(null);

const occasionOptions: { value: Occasion; label: string; hint: string }[] = [
  { value: "DAILY", label: "日常休闲", hint: "轻松、自然" },
  { value: "SCHOOL", label: "上学", hint: "舒适、有精神" },
  { value: "WORK", label: "通勤", hint: "利落、得体" },
  { value: "INTERVIEW", label: "面试", hint: "可靠、有分寸" },
  { value: "DATE", label: "约会", hint: "亲和、有记忆点" },
  { value: "SOCIAL", label: "聚会", hint: "轻松、有表现力" },
  { value: "TRAVEL", label: "旅行", hint: "耐走、上镜" },
  { value: "OTHER", label: "其他", hint: "按穿搭本身分析" },
];

const isUploading = computed(() =>
  ["authorizing", "uploading", "completing"].includes(phase.value),
);

const actionLabel = computed(() => {
  if (submitting.value) {
    return "正在创建诊断";
  }
  if (isUploading.value) {
    return "正在安全上传";
  }
  if (phase.value === "ready") {
    return selectedOccasion.value ? "开始穿搭诊断" : "选择这次的场景";
  }
  if (image.value) {
    return "重新选择照片";
  }
  return "上传今天的穿搭";
});

const phaseLabel = computed(() => {
  const labels = {
    selected: "照片已保存为本地草稿",
    authorizing: "正在申请单次上传权限",
    uploading: "正在直传私有空间",
    completing: "正在校验图片安全性",
    ready: "照片已就绪",
    failed: "上传未完成",
    cancelled: "上传已暂停",
    idle: "",
  };
  return labels[phase.value];
});

const openTasks = () => {
  uni.navigateTo({ url: "/pages/tasks/index" });
};

const openProfile = () => {
  uni.navigateTo({ url: "/pages/profile/index" });
};

const openRecentResult = () => {
  if (!recentDiagnosisId.value) {
    return;
  }
  uni.navigateTo({
    url: `/pages/diagnosis/result?id=${recentDiagnosisId.value}`,
  });
};

const startDiagnosis = async () => {
  if (phase.value === "ready") {
    if (!selectedOccasion.value) {
      uni.showToast({ title: "请先选择一个主要场景", icon: "none" });
      return;
    }
    if (!assets.assetId) {
      uni.showToast({ title: "照片状态异常，请重新上传", icon: "none" });
      return;
    }
    try {
      const diagnosis = await diagnoses.create(
        assets.assetId,
        selectedOccasion.value,
      );
      uni.navigateTo({ url: `/pages/diagnosis/index?id=${diagnosis.id}` });
    } catch {
      if (diagnosisErrorCode.value === "AI_CONSENT_REQUIRED") {
        uni.showModal({
          title: "需要你的明确授权",
          content:
            "开启后，AI 只会处理你主动提交的照片，用于生成本次穿搭建议。",
          confirmText: "去设置",
          success(result) {
            if (result.confirm) {
              openProfile();
            }
          },
        });
      }
    }
    return;
  }
  void assets.selectAndUpload();
};

onShow(() => {
  void jobs.refresh();
  if (recentDiagnosisId.value) {
    void diagnoses.refresh(recentDiagnosisId.value);
  }
});
</script>

<template>
  <view class="page-shell">
    <view class="ambient ambient--top" />
    <view class="ambient ambient--bottom" />

    <header class="masthead">
      <view>
        <text class="eyebrow">AI WARDROBE · 今日</text>
        <text class="date">把今天，穿成你自己。</text>
      </view>
      <view class="masthead__actions">
        <button class="task-link" aria-label="打开个人设置" @click="openProfile">
          <text>我的</text>
        </button>
        <button class="task-link" aria-label="打开任务中心" @click="openTasks">
          <text v-if="hasPendingJobs" class="task-link__dot" />
          <text>任务</text>
        </button>
      </view>
    </header>

    <main>
      <section class="hero">
        <text class="hero__kicker">一张照片，先看懂再改变</text>
        <text class="hero__title">不是换一个人，{{
          "\n"
        }}只是把你穿得更好。</text>
        <text class="hero__copy">
          保留你喜欢的衣服和气质，只给出达到明显改善所需要的最少修改。
        </text>
      </section>

      <button
        v-if="
          currentDiagnosis?.job_status === 'COMPLETED' &&
          currentDiagnosis.result
        "
        class="recent-result"
        @click="openRecentResult"
      >
        <view>
          <text class="recent-result__eyebrow">最近一次诊断</text>
          <text class="recent-result__summary">{{
            currentDiagnosis.result.summary
          }}</text>
        </view>
        <text class="recent-result__score"
          >{{ currentDiagnosis.result.score
          }}<text class="recent-result__unit">/100</text></text
        >
      </button>

      <section class="diagnosis-card">
        <view class="diagnosis-card__index">
          <text>01</text>
          <text class="diagnosis-card__rule" />
          <text>INSTANT DIAGNOSIS</text>
        </view>

        <view class="photo-stage">
          <view class="photo-stage__frame">
            <image
              v-if="image"
              class="source-photo"
              :src="image.localPath"
              mode="aspectFill"
            />
            <view v-else class="silhouette">
              <view class="silhouette__head" />
              <view class="silhouette__body" />
              <view class="silhouette__leg silhouette__leg--left" />
              <view class="silhouette__leg silhouette__leg--right" />
            </view>
          </view>
          <view class="photo-stage__note">
            <text class="photo-stage__note-number">3</text>
            <text class="photo-stage__note-copy">秒内知道{{
              "\n"
            }}系统已开始工作</text>
          </view>
        </view>

        <view v-if="image" class="upload-status" aria-live="polite">
          <view class="upload-status__line">
            <text>{{ phaseLabel }}</text>
            <text>{{ progress }}%</text>
          </view>
          <view class="upload-status__track">
            <view
              class="upload-status__fill"
              :style="{ width: `${progress}%` }"
            />
          </view>
          <text v-if="assetErrorMessage" class="upload-status__error">
            {{ assetErrorMessage }}
          </text>
          <view class="upload-status__actions">
            <button
              v-if="isUploading"
              class="text-action"
              @click="assets.cancelUpload"
            >
              暂停
            </button>
            <button
              v-if="phase === 'failed' || phase === 'cancelled'"
              class="text-action"
              @click="assets.upload"
            >
              重试
            </button>
            <button
              v-if="phase !== 'idle' && !isUploading"
              class="text-action text-action--quiet"
              @click="assets.clearDraft"
            >
              删除草稿
            </button>
          </view>
        </view>

        <view v-if="phase === 'ready'" class="occasion-panel">
          <view class="occasion-panel__heading">
            <view>
              <text class="occasion-panel__eyebrow">02 · OCCASION</text>
              <text class="occasion-panel__title">这身衣服，主要穿去哪里？</text>
            </view>
            <text class="occasion-panel__note">单选</text>
          </view>
          <view class="occasion-grid">
            <button
              v-for="option in occasionOptions"
              :key="option.value"
              class="occasion-option"
              :class="{
                'occasion-option--selected':
                  selectedOccasion === option.value,
              }"
              :aria-pressed="selectedOccasion === option.value"
              @click="selectedOccasion = option.value"
            >
              <text class="occasion-option__label">{{ option.label }}</text>
              <text class="occasion-option__hint">{{ option.hint }}</text>
            </button>
          </view>
        </view>

        <text v-if="diagnosisErrorMessage" class="diagnosis-error">
          {{ diagnosisErrorMessage }}
        </text>

        <button
          class="primary-action"
          :disabled="isUploading || submitting"
          @click="startDiagnosis"
        >
          <text>{{ actionLabel }}</text>
          <text class="primary-action__arrow">↗</text>
        </button>

        <view class="privacy-note">
          <text class="privacy-note__mark">私</text>
          <text>照片默认仅你可见，可随时删除</text>
        </view>
      </section>

      <section class="promise-strip">
        <view class="promise">
          <text class="promise__value">≤20s</text>
          <text class="promise__label">文字诊断 P90</text>
        </view>
        <view class="promise">
          <text class="promise__value">1–2</text>
          <text class="promise__label">最多替换件数</text>
        </view>
        <view class="promise">
          <text class="promise__value">0</text>
          <text class="promise__label">失败消耗次数</text>
        </view>
      </section>
    </main>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.page-shell {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  padding: calc(env(safe-area-inset-top) + 36rpx) 32rpx 80rpx;
  background:
    linear-gradient(120deg, rgba(255, 253, 248, 0.9), rgba(244, 240, 232, 0.64)),
    $color-paper;
}

.ambient {
  position: absolute;
  width: 420rpx;
  height: 420rpx;
  border-radius: 50%;
  pointer-events: none;
  filter: blur(4rpx);

  &--top {
    top: -220rpx;
    right: -180rpx;
    background: rgba($color-vermilion, 0.16);
  }

  &--bottom {
    bottom: 100rpx;
    left: -300rpx;
    background: rgba($color-sage, 0.2);
  }
}

.masthead {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;

  &__actions {
    display: flex;
    gap: 12rpx;
  }
}

.eyebrow {
  display: block;
  color: $color-sage-deep;
  font-size: 20rpx;
  font-weight: 700;
  letter-spacing: 4rpx;
}

.date {
  display: block;
  margin-top: 10rpx;
  color: $color-muted;
  font-size: 22rpx;
}

.task-link {
  display: flex;
  align-items: center;
  gap: 12rpx;
  height: 64rpx;
  margin: 0;
  padding: 0 24rpx;
  border: 1rpx solid rgba($color-ink, 0.16);
  border-radius: 999rpx;
  background: rgba($color-white, 0.7);
  color: $color-ink;
  font-size: 24rpx;
  line-height: 64rpx;
  backdrop-filter: blur(20rpx);

  &__dot {
    width: 10rpx;
    height: 10rpx;
    border-radius: 50%;
    background: $color-vermilion;
    box-shadow: 0 0 0 8rpx rgba($color-vermilion, 0.12);
  }
}

.hero {
  position: relative;
  z-index: 1;
  margin-top: 92rpx;

  &__kicker {
    display: block;
    color: $color-vermilion;
    font-size: 22rpx;
    font-weight: 700;
  }

  &__title {
    display: block;
    margin-top: 22rpx;
    color: $color-ink;
    font-family:
      "Songti SC",
      "STSong",
      serif;
    font-size: 68rpx;
    font-weight: 700;
    line-height: 1.18;
    letter-spacing: -3rpx;
  }

  &__copy {
    display: block;
    max-width: 600rpx;
    margin-top: 28rpx;
    color: $color-muted;
    font-size: 26rpx;
    line-height: 1.75;
  }
}

.diagnosis-card {
  position: relative;
  z-index: 1;
  margin-top: 56rpx;
  padding: 28rpx;
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: rgba($color-white, 0.92);
  box-shadow: $shadow-soft;

  &__index {
    display: flex;
    align-items: center;
    gap: 16rpx;
    color: $color-muted;
    font-size: 18rpx;
    letter-spacing: 2rpx;
  }

  &__rule {
    width: 72rpx;
    height: 1rpx;
    background: rgba($color-ink, 0.22);
  }
}

.recent-result {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 132rpx;
  margin-top: 42rpx;
  padding: 24rpx 28rpx;
  border: 1rpx solid rgba($color-sage-deep, 0.18);
  border-radius: $radius-medium;
  background: rgba($color-white, 0.8);
  text-align: left;

  &__eyebrow {
    display: block;
    color: $color-sage-deep;
    font-size: 19rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__summary {
    display: -webkit-box;
    max-width: 480rpx;
    margin-top: 10rpx;
    overflow: hidden;
    color: $color-ink;
    font-size: 23rpx;
    line-height: 1.45;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }

  &__score {
    flex: 0 0 auto;
    margin-left: 18rpx;
    color: $color-vermilion;
    font-family: "Songti SC", serif;
    font-size: 52rpx;
    font-weight: 700;
    line-height: 1;

    &__unit {
      color: $color-muted;
      font-size: 18rpx;
      font-weight: 400;
    }
  }
}

.photo-stage {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 160rpx;
  gap: 18rpx;
  margin-top: 24rpx;

  &__frame {
    position: relative;
    height: 390rpx;
    overflow: hidden;
    border-radius: $radius-medium;
    background:
      linear-gradient(145deg, rgba($color-sage, 0.92), rgba($color-sage-deep, 0.96));
  }

  &__frame::before {
    position: absolute;
    top: 28rpx;
    left: 28rpx;
    width: 54rpx;
    height: 54rpx;
    border-top: 2rpx solid rgba($color-white, 0.72);
    border-left: 2rpx solid rgba($color-white, 0.72);
    content: "";
  }

  &__note {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    min-height: 390rpx;
    padding: 24rpx 18rpx;
    border-radius: $radius-medium;
    background: $color-paper-deep;
  }

  &__note-number {
    color: $color-vermilion;
    font-family:
      "Songti SC",
      serif;
    font-size: 76rpx;
    line-height: 1;
  }

  &__note-copy {
    margin-top: 14rpx;
    color: $color-ink;
    font-size: 20rpx;
    line-height: 1.55;
  }
}

.silhouette {
  position: absolute;
  bottom: -10rpx;
  left: 50%;
  width: 190rpx;
  height: 340rpx;
  transform: translateX(-50%);

  &__head {
    width: 78rpx;
    height: 88rpx;
    margin: 0 auto;
    border-radius: 44% 44% 48% 48%;
    background: rgba($color-white, 0.88);
  }

  &__body {
    width: 170rpx;
    height: 190rpx;
    margin: -4rpx auto 0;
    border-radius: 60rpx 60rpx 34rpx 34rpx;
    background: rgba($color-white, 0.88);
  }

  &__leg {
    position: absolute;
    bottom: 0;
    width: 62rpx;
    height: 120rpx;
    border-radius: 0 0 22rpx 22rpx;
    background: rgba($color-white, 0.88);

    &--left {
      left: 28rpx;
      transform: rotate(3deg);
    }

    &--right {
      right: 28rpx;
      transform: rotate(-3deg);
    }
  }
}

.source-photo {
  width: 100%;
  height: 100%;
}

.upload-status {
  margin-top: 24rpx;
  padding: 22rpx 24rpx;
  border-radius: $radius-medium;
  background: $color-paper-deep;

  &__line {
    display: flex;
    justify-content: space-between;
    color: $color-ink;
    font-size: 21rpx;
  }

  &__track {
    height: 8rpx;
    margin-top: 16rpx;
    overflow: hidden;
    border-radius: 999rpx;
    background: rgba($color-ink, 0.1);
  }

  &__fill {
    height: 100%;
    border-radius: inherit;
    background: $color-vermilion;
    transition: width 240ms ease;
  }

  &__error {
    display: block;
    margin-top: 14rpx;
    color: $color-vermilion;
    font-size: 20rpx;
    line-height: 1.5;
  }

  &__actions {
    display: flex;
    gap: 16rpx;
    margin-top: 14rpx;
  }
}

.occasion-panel {
  margin-top: 26rpx;
  padding-top: 28rpx;
  border-top: 1rpx solid rgba($color-ink, 0.1);

  &__heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
  }

  &__eyebrow {
    display: block;
    color: $color-vermilion;
    font-size: 18rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__title {
    display: block;
    margin-top: 10rpx;
    color: $color-ink;
    font-family: "Songti SC", serif;
    font-size: 32rpx;
    font-weight: 700;
  }

  &__note {
    padding: 8rpx 14rpx;
    border-radius: 999rpx;
    background: $color-paper-deep;
    color: $color-muted;
    font-size: 18rpx;
  }
}

.occasion-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14rpx;
  margin-top: 24rpx;
}

.occasion-option {
  min-height: 98rpx;
  margin: 0;
  padding: 18rpx 20rpx;
  border: 1rpx solid rgba($color-ink, 0.1);
  border-radius: $radius-small;
  background: $color-paper;
  text-align: left;
  line-height: 1.2;

  &--selected {
    border-color: $color-sage-deep;
    background: rgba($color-sage, 0.2);
    box-shadow: inset 0 0 0 1rpx $color-sage-deep;
  }

  &__label {
    display: block;
    color: $color-ink;
    font-size: 24rpx;
    font-weight: 700;
  }

  &__hint {
    display: block;
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 18rpx;
  }
}

.diagnosis-error {
  display: block;
  margin-top: 22rpx;
  padding: 20rpx 22rpx;
  border-radius: $radius-small;
  background: rgba($color-vermilion, 0.09);
  color: $color-vermilion;
  font-size: 21rpx;
  line-height: 1.5;
}

.text-action {
  width: auto;
  height: 54rpx;
  margin: 0;
  padding: 0 22rpx;
  border: 1rpx solid rgba($color-vermilion, 0.34);
  border-radius: 999rpx;
  background: rgba($color-white, 0.7);
  color: $color-vermilion;
  font-size: 20rpx;
  line-height: 54rpx;

  &--quiet {
    border-color: rgba($color-ink, 0.14);
    color: $color-muted;
  }
}

.primary-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 104rpx;
  margin-top: 24rpx;
  padding: 0 34rpx;
  border-radius: 999rpx;
  background: $color-ink;
  color: $color-white;
  font-size: 28rpx;
  font-weight: 700;
  line-height: 104rpx;

  &[disabled] {
    opacity: 0.58;
  }

  &__arrow {
    color: $color-vermilion;
    font-family: serif;
    font-size: 42rpx;
  }
}

.privacy-note {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  margin-top: 22rpx;
  color: $color-muted;
  font-size: 20rpx;

  &__mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34rpx;
    height: 34rpx;
    border: 1rpx solid rgba($color-sage-deep, 0.4);
    border-radius: 50%;
    color: $color-sage-deep;
    font-family: "Songti SC", serif;
    font-size: 18rpx;
  }
}

.promise-strip {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 28rpx;
  padding: 28rpx 0;
  border-top: 1rpx solid rgba($color-ink, 0.12);
  border-bottom: 1rpx solid rgba($color-ink, 0.12);
}

.promise {
  text-align: center;

  & + & {
    border-left: 1rpx solid rgba($color-ink, 0.1);
  }

  &__value {
    display: block;
    color: $color-ink;
    font-family:
      "Songti SC",
      serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__label {
    display: block;
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 18rpx;
  }
}
</style>
