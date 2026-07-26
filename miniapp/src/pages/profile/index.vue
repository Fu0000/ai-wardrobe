<script setup lang="ts">
import { onLoad } from "@dcloudio/uni-app";
import { computed, ref } from "vue";

import { getProfile, updateProfile } from "@/services/profile";
import { useAuthStore } from "@/stores/auth";

const CONSENT_VERSION = "privacy-v1";
const auth = useAuthStore();
const displayName = ref("");
const hasConsent = ref(false);
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref<string | null>(null);

const saveLabel = computed(() => (saving.value ? "正在保存" : "保存设置"));

const updateConsent = (event: unknown) => {
  const change = event as { detail?: { value?: boolean } };
  hasConsent.value = Boolean(change.detail?.value);
};

const requireToken = async (): Promise<string | null> => {
  await auth.authenticate();
  if (!auth.accessToken) {
    errorMessage.value = "登录失败，请检查网络后重试。";
    return null;
  }
  return auth.accessToken;
};

const loadProfile = async () => {
  loading.value = true;
  errorMessage.value = null;
  const token = await requireToken();
  if (!token) {
    loading.value = false;
    return;
  }
  try {
    const profile = await getProfile(token);
    displayName.value = profile.display_name ?? "";
    hasConsent.value = profile.has_ai_processing_consent;
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : "个人资料加载失败。";
  } finally {
    loading.value = false;
  }
};

const save = async () => {
  const token = await requireToken();
  if (!token || saving.value) {
    return;
  }
  saving.value = true;
  errorMessage.value = null;
  try {
    await updateProfile(
      {
        display_name: displayName.value.trim() || null,
        has_ai_processing_consent: hasConsent.value,
        ...(hasConsent.value ? { consent_version: CONSENT_VERSION } : {}),
      },
      token,
    );
    uni.showToast({ title: "设置已保存", icon: "success" });
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : "保存失败，请重试。";
  } finally {
    saving.value = false;
  }
};

const openDeletion = () => {
  uni.navigateTo({ url: "/pages/profile/deletion" });
};

const openPhotoDeletion = () => {
  uni.navigateTo({ url: "/pages/profile/photos" });
};

const openFeedback = () => {
  uni.navigateTo({ url: "/pages/profile/feedback" });
};

onLoad(() => {
  void loadProfile();
});
</script>

<template>
  <view class="page-shell">
    <header class="page-header">
      <text class="eyebrow">YOUR WARDROBE · 账户</text>
      <text class="title">把边界说清楚，才值得信任。</text>
      <text class="subtitle">你可以随时修改昵称，或撤回 AI 处理授权。</text>
    </header>

    <view v-if="loading" class="state-card">
      <text>正在加载个人设置…</text>
    </view>

    <main v-else class="settings-card">
      <label class="field">
        <text class="field__label">称呼</text>
        <input
          v-model="displayName"
          class="field__input"
          maxlength="40"
          placeholder="希望我们怎么称呼你"
        />
      </label>

      <view class="consent">
        <view class="consent__copy">
          <text class="consent__title">允许 AI 分析穿搭照片</text>
          <text class="consent__body">
            仅用于生成本次诊断与优化结果；照片默认私有，可在应用内删除。撤回后不再创建新的
            AI 任务。
          </text>
        </view>
        <switch
          :checked="hasConsent"
          color="#C45B3E"
          aria-label="AI 处理授权"
          @change="updateConsent"
        />
      </view>

      <view class="data-note">
        <text class="data-note__mark">私</text>
        <view>
          <text class="data-note__title">默认私有</text>
          <text class="data-note__body">
            原图不会进入公开分享页，访问地址具有有效期并绑定你的账户。
          </text>
        </view>
      </view>

      <text v-if="errorMessage" class="error-message">{{ errorMessage }}</text>

      <button class="save-action" :disabled="saving" @click="save">
        {{ saveLabel }}
      </button>

      <view class="data-control">
        <view>
          <text class="data-control__title">删除当前照片与派生结果</text>
          <text class="data-control__copy">
            可单独删除原图、关联诊断、优化图和分享卡片，不影响账户。
          </text>
        </view>
        <button @click="openPhotoDeletion">管理照片</button>
      </view>

      <view class="data-control feedback-control">
        <view>
          <text class="data-control__title">内测反馈</text>
          <text class="data-control__copy">
            反馈 AI 结果、功能异常或体验问题，可保留草稿并关联排障上下文。
          </text>
        </view>
        <button @click="openFeedback">写反馈</button>
      </view>

      <view class="danger-zone">
        <text class="danger-zone__eyebrow">DANGER ZONE</text>
        <text class="danger-zone__title">永久删除账户与数据</text>
        <text class="danger-zone__copy">
          可查询处理状态；删除完成后无法恢复原图、诊断、优化和分享。
        </text>
        <button @click="openDeletion">查看删除选项</button>
      </view>
    </main>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.page-shell {
  min-height: 100vh;
  padding: 56rpx 32rpx 80rpx;
  background:
    radial-gradient(circle at 90% 5%, rgba($color-vermilion, 0.12), transparent 34%),
    $color-paper;
}

.page-header {
  display: block;
  padding: 24rpx 4rpx 44rpx;
}

.eyebrow {
  display: block;
  color: $color-sage-deep;
  font-size: 20rpx;
  font-weight: 700;
  letter-spacing: 3rpx;
}

.title {
  display: block;
  margin-top: 22rpx;
  color: $color-ink;
  font-family: "Songti SC", "STSong", serif;
  font-size: 54rpx;
  font-weight: 700;
  line-height: 1.24;
}

.subtitle {
  display: block;
  margin-top: 18rpx;
  color: $color-muted;
  font-size: 23rpx;
  line-height: 1.6;
}

.state-card,
.settings-card {
  border: 1rpx solid rgba($color-ink, 0.08);
  border-radius: $radius-large;
  background: rgba($color-white, 0.94);
  box-shadow: $shadow-soft;
}

.state-card {
  padding: 48rpx 32rpx;
  color: $color-muted;
  font-size: 24rpx;
}

.settings-card {
  padding: 32rpx;
}

.field {
  display: block;

  &__label {
    display: block;
    color: $color-muted;
    font-size: 20rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__input {
    height: 92rpx;
    margin-top: 14rpx;
    padding: 0 24rpx;
    border: 1rpx solid rgba($color-ink, 0.12);
    border-radius: $radius-medium;
    background: $color-paper;
    color: $color-ink;
    font-size: 27rpx;
  }
}

.consent {
  display: flex;
  gap: 24rpx;
  align-items: flex-start;
  justify-content: space-between;
  margin-top: 32rpx;
  padding: 28rpx 0;
  border-top: 1rpx solid rgba($color-ink, 0.09);
  border-bottom: 1rpx solid rgba($color-ink, 0.09);

  &__copy {
    flex: 1;
  }

  &__title {
    display: block;
    color: $color-ink;
    font-size: 27rpx;
    font-weight: 700;
  }

  &__body {
    display: block;
    margin-top: 12rpx;
    color: $color-muted;
    font-size: 21rpx;
    line-height: 1.65;
  }
}

.data-note {
  display: flex;
  gap: 18rpx;
  margin-top: 28rpx;
  padding: 24rpx;
  border-radius: $radius-medium;
  background: $color-paper-deep;

  &__mark {
    display: flex;
    flex: 0 0 48rpx;
    align-items: center;
    justify-content: center;
    width: 48rpx;
    height: 48rpx;
    border: 1rpx solid rgba($color-sage-deep, 0.4);
    border-radius: 50%;
    color: $color-sage-deep;
    font-family: "Songti SC", serif;
    font-size: 22rpx;
  }

  &__title {
    display: block;
    color: $color-ink;
    font-size: 22rpx;
    font-weight: 700;
  }

  &__body {
    display: block;
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 20rpx;
    line-height: 1.55;
  }
}

.error-message {
  display: block;
  margin-top: 24rpx;
  color: $color-vermilion;
  font-size: 21rpx;
}

.save-action {
  height: 96rpx;
  margin-top: 32rpx;
  border-radius: 999rpx;
  background: $color-ink;
  color: $color-white;
  font-size: 27rpx;
  font-weight: 700;
  line-height: 96rpx;

  &[disabled] {
    opacity: 0.58;
  }
}

.data-control {
  display: flex;
  gap: 20rpx;
  align-items: center;
  justify-content: space-between;
  margin-top: 34rpx;
  padding: 26rpx;
  border-radius: $radius-medium;
  background: $color-paper-deep;

  &__title,
  &__copy {
    display: block;
  }

  &__title {
    color: $color-ink;
    font-size: 23rpx;
    font-weight: 700;
  }

  &__copy {
    max-width: 400rpx;
    margin-top: 8rpx;
    color: $color-muted;
    font-size: 19rpx;
    line-height: 1.55;
  }

  button {
    flex: 0 0 150rpx;
    height: 68rpx;
    border: 1rpx solid rgba($color-ink, 0.18);
    border-radius: 999rpx;
    background: $color-white;
    color: $color-ink;
    font-size: 20rpx;
    line-height: 66rpx;
  }
}

.danger-zone {
  margin-top: 34rpx;
  padding-top: 30rpx;
  border-top: 1rpx solid rgba($color-vermilion, 0.22);

  &__eyebrow,
  &__title,
  &__copy {
    display: block;
  }

  &__eyebrow {
    color: $color-vermilion;
    font-size: 17rpx;
    font-weight: 700;
    letter-spacing: 2rpx;
  }

  &__title {
    margin-top: 10rpx;
    color: $color-ink;
    font-size: 25rpx;
    font-weight: 700;
  }

  &__copy {
    margin-top: 10rpx;
    color: $color-muted;
    font-size: 20rpx;
    line-height: 1.6;
  }

  button {
    height: 82rpx;
    margin-top: 20rpx;
    border: 1rpx solid rgba($color-vermilion, 0.46);
    border-radius: 999rpx;
    background: transparent;
    color: $color-vermilion;
    font-size: 22rpx;
    line-height: 80rpx;
  }
}
</style>
