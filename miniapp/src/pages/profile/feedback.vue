<script setup lang="ts">
import { onLoad } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import {
  createFeedback,
  type FeedbackCategory,
} from "@/services/feedback";
import { useAuthStore } from "@/stores/auth";

const DRAFT_KEY = "aiw:beta-feedback:v1";

interface FeedbackDraft {
  category: FeedbackCategory;
  rating: number | null;
  message: string;
  idempotencyKey: string;
  relatedJobId: string | null;
  originPage: string | null;
  traceId: string | null;
}

const categories: Array<{ value: FeedbackCategory; label: string }> = [
  { value: "AI_QUALITY", label: "AI 结果" },
  { value: "BUG", label: "功能异常" },
  { value: "EXPERIENCE", label: "使用体验" },
  { value: "PRIVACY", label: "隐私与数据" },
  { value: "OTHER", label: "其他" },
];

const auth = useAuthStore();
const category = ref<FeedbackCategory>("AI_QUALITY");
const rating = ref<number | null>(null);
const message = ref("");
const idempotencyKey = ref("");
const relatedJobId = ref<string | null>(null);
const originPage = ref<string | null>(null);
const traceId = ref<string | null>(null);
const submitting = ref(false);
const submitted = ref(false);
const errorMessage = ref<string | null>(null);

const normalizedMessage = computed(() => message.value.trim().replace(/\s+/g, " "));
const canSubmit = computed(
  () =>
    normalizedMessage.value.length >= 10 &&
    normalizedMessage.value.length <= 2_000 &&
    !submitting.value,
);

const newIdempotencyKey = () =>
  `feedback-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;

const persistDraft = () => {
  const draft: FeedbackDraft = {
    category: category.value,
    rating: rating.value,
    message: message.value,
    idempotencyKey: idempotencyKey.value,
    relatedJobId: relatedJobId.value,
    originPage: originPage.value,
    traceId: traceId.value,
  };
  uni.setStorageSync(DRAFT_KEY, draft);
};

const chooseCategory = (value: FeedbackCategory) => {
  category.value = value;
  persistDraft();
};

const chooseRating = (value: number) => {
  rating.value = rating.value === value ? null : value;
  persistDraft();
};

const safeSystemValue = (
  systemInfo: Record<string, unknown>,
  key: string,
  maxLength: number,
): string | undefined => {
  const value = systemInfo[key];
  if (typeof value !== "string" || !value.trim()) {
    return undefined;
  }
  return value.trim().slice(0, maxLength);
};

const networkType = async (): Promise<string | undefined> =>
  new Promise((resolve) => {
    uni.getNetworkType({
      success: (result) => resolve(result.networkType),
      fail: () => resolve(undefined),
    });
  });

const submit = async () => {
  if (!canSubmit.value) {
    errorMessage.value = "请至少写 10 个字，帮助我们准确理解问题。";
    return;
  }
  submitting.value = true;
  errorMessage.value = null;
  persistDraft();
  try {
    await auth.authenticate();
    if (!auth.accessToken) {
      throw new Error("登录失败，请检查网络后重试。");
    }
    const systemInfo = uni.getSystemInfoSync() as unknown as Record<
      string,
      unknown
    >;
    await createFeedback(
      {
        category: category.value,
        rating: rating.value,
        message: normalizedMessage.value,
        ...(relatedJobId.value ? { related_job_id: relatedJobId.value } : {}),
        ...(originPage.value ? { page: originPage.value } : {}),
        ...(traceId.value ? { trace_id: traceId.value } : {}),
        app_version: safeSystemValue(systemInfo, "appVersion", 40),
        platform: safeSystemValue(systemInfo, "platform", 40),
        system_version: safeSystemValue(systemInfo, "system", 80),
        wechat_version: safeSystemValue(systemInfo, "version", 40),
        network_type: await networkType(),
      },
      idempotencyKey.value,
      auth.accessToken,
    );
    submitted.value = true;
    uni.removeStorageSync(DRAFT_KEY);
    uni.showToast({ title: "反馈已收到", icon: "success" });
  } catch (error) {
    errorMessage.value =
      error instanceof Error
        ? error.message
        : "反馈暂时没有提交成功，请稍后重试。";
  } finally {
    submitting.value = false;
  }
};

const createAnother = () => {
  category.value = "AI_QUALITY";
  rating.value = null;
  message.value = "";
  idempotencyKey.value = newIdempotencyKey();
  relatedJobId.value = null;
  originPage.value = "pages/profile/feedback";
  traceId.value = null;
  submitted.value = false;
  errorMessage.value = null;
  persistDraft();
};

onLoad((options) => {
  const persisted = uni.getStorageSync(DRAFT_KEY) as FeedbackDraft | "";
  if (persisted) {
    category.value = persisted.category;
    rating.value = persisted.rating;
    message.value = persisted.message;
    idempotencyKey.value = persisted.idempotencyKey;
    relatedJobId.value = persisted.relatedJobId;
    originPage.value = persisted.originPage;
    traceId.value = persisted.traceId;
  } else {
    idempotencyKey.value = newIdempotencyKey();
  }
  if (typeof options?.jobId === "string") {
    relatedJobId.value = options.jobId;
  }
  if (
    typeof options?.traceId === "string" &&
    /^[0-9a-f]{32}$/.test(options.traceId)
  ) {
    traceId.value = options.traceId;
  }
  if (typeof options?.from === "string") {
    originPage.value = options.from.slice(0, 120);
  } else if (!originPage.value) {
    originPage.value = "pages/profile/feedback";
  }
  persistDraft();
});
</script>

<template>
  <view class="feedback-page">
    <header class="hero">
      <text class="eyebrow">CLOSED BETA · 共创</text>
      <text class="title">哪里不像你期待的那样？</text>
      <text class="subtitle">
        每条反馈都会进入版本分诊；说清场景和实际结果，比一句“不好用”更能帮到我们。
      </text>
    </header>

    <view v-if="submitted" class="success-card">
      <text class="success-card__mark">✓</text>
      <text class="success-card__title">谢谢，反馈已经入列。</text>
      <text class="success-card__copy">
        我们不会在反馈中保存照片、访问令牌或图片地址。需要进一步确认时，会通过内测群联系。
      </text>
      <button @click="createAnother">再写一条</button>
    </view>

    <main v-else class="form-card">
      <view class="section">
        <text class="section__label">反馈类型</text>
        <view class="category-grid">
          <button
            v-for="item in categories"
            :key="item.value"
            :class="{ active: category === item.value }"
            @click="chooseCategory(item.value)"
          >
            {{ item.label }}
          </button>
        </view>
      </view>

      <view class="section">
        <text class="section__label">整体感受（可选）</text>
        <view class="rating-row" aria-label="1 到 5 分">
          <button
            v-for="value in 5"
            :key="value"
            :class="{ active: rating !== null && value <= rating }"
            @click="chooseRating(value)"
          >
            {{ value }}
          </button>
        </view>
      </view>

      <label class="section">
        <text class="section__label">发生了什么</text>
        <textarea
          v-model="message"
          class="message-input"
          maxlength="2000"
          placeholder="例如：我选择了通勤场景，但优化图改变了原本的外套版型；我希望只调整鞋和配色。"
          @input="persistDraft"
        />
        <text class="counter">{{ normalizedMessage.length }} / 2000</text>
      </label>

      <view class="privacy-note">
        <text class="privacy-note__title">最小化收集</text>
        <text class="privacy-note__copy">
          仅附带页面、系统、微信版本和网络类型用于定位；不上传照片内容、图片地址、OpenID
          或访问令牌。
        </text>
      </view>

      <text v-if="errorMessage" class="error-message">{{ errorMessage }}</text>
      <button
        class="submit-action"
        :disabled="!canSubmit"
        @click="submit"
      >
        {{ submitting ? "正在提交…" : "提交反馈" }}
      </button>
    </main>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.feedback-page {
  min-height: 100vh;
  padding: 48rpx 32rpx 80rpx;
  background:
    radial-gradient(circle at 88% 4%, rgba($color-sage-deep, 0.14), transparent 32%),
    $color-paper;
}

.hero {
  display: block;
  padding: 24rpx 4rpx 40rpx;
}

.eyebrow,
.title,
.subtitle {
  display: block;
}

.eyebrow {
  color: $color-vermilion;
  font-size: 19rpx;
  font-weight: 700;
  letter-spacing: 3rpx;
}

.title {
  margin-top: 20rpx;
  color: $color-ink;
  font-family: "Songti SC", "STSong", serif;
  font-size: 52rpx;
  font-weight: 700;
  line-height: 1.25;
}

.subtitle {
  margin-top: 18rpx;
  color: $color-muted;
  font-size: 22rpx;
  line-height: 1.7;
}

.form-card,
.success-card {
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: rgba($color-white, 0.95);
  box-shadow: $shadow-soft;
}

.form-card {
  padding: 32rpx;
}

.section {
  display: block;
  margin-top: 32rpx;

  &:first-child {
    margin-top: 0;
  }

  &__label {
    display: block;
    margin-bottom: 16rpx;
    color: $color-ink;
    font-size: 22rpx;
    font-weight: 700;
  }
}

.category-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;

  button {
    height: 68rpx;
    border: 1rpx solid rgba($color-ink, 0.12);
    border-radius: 999rpx;
    background: $color-paper;
    color: $color-muted;
    font-size: 19rpx;
    line-height: 66rpx;

    &.active {
      border-color: $color-ink;
      background: $color-ink;
      color: $color-white;
    }
  }
}

.rating-row {
  display: flex;
  gap: 16rpx;

  button {
    width: 64rpx;
    height: 64rpx;
    margin: 0;
    padding: 0;
    border: 1rpx solid rgba($color-vermilion, 0.28);
    border-radius: 50%;
    background: transparent;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 62rpx;

    &.active {
      border-color: $color-vermilion;
      background: $color-vermilion;
      color: $color-white;
    }
  }
}

.message-input {
  box-sizing: border-box;
  width: 100%;
  min-height: 300rpx;
  padding: 24rpx;
  border: 1rpx solid rgba($color-ink, 0.12);
  border-radius: $radius-medium;
  background: $color-paper;
  color: $color-ink;
  font-size: 24rpx;
  line-height: 1.65;
}

.counter {
  display: block;
  margin-top: 10rpx;
  color: $color-muted;
  font-size: 18rpx;
  text-align: right;
}

.privacy-note {
  margin-top: 28rpx;
  padding: 22rpx;
  border-left: 4rpx solid $color-sage-deep;
  border-radius: 0 $radius-small $radius-small 0;
  background: rgba($color-sage-deep, 0.07);

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    color: $color-sage-deep;
    font-size: 20rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 19rpx;
    line-height: 1.6;
  }
}

.error-message {
  display: block;
  margin-top: 22rpx;
  color: $color-vermilion;
  font-size: 20rpx;
}

.submit-action,
.success-card button {
  height: 92rpx;
  margin-top: 28rpx;
  border-radius: 999rpx;
  background: $color-ink;
  color: $color-white;
  font-size: 25rpx;
  font-weight: 700;
  line-height: 92rpx;

  &[disabled] {
    opacity: 0.42;
  }
}

.success-card {
  padding: 52rpx 34rpx;
  text-align: center;

  &__mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 76rpx;
    height: 76rpx;
    margin: 0 auto;
    border-radius: 50%;
    background: $color-sage-deep;
    color: $color-white;
    font-size: 32rpx;
  }

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    margin-top: 24rpx;
    color: $color-ink;
    font-family: "Songti SC", serif;
    font-size: 34rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 16rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.7;
  }
}
</style>
