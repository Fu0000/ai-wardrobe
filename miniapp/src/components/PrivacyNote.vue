<script setup lang="ts">
type PrivacyNoteTone = "light" | "dark";
type PrivacyNoteSpacing = "compact" | "normal";

withDefaults(
  defineProps<{
    message: string;
    mark?: string;
    showMark?: boolean;
    tone?: PrivacyNoteTone;
    spacing?: PrivacyNoteSpacing;
  }>(),
  {
    mark: "私",
    showMark: true,
    tone: "light",
    spacing: "normal",
  },
);
</script>

<template>
  <view
    class="privacy-note"
    :class="[
      `privacy-note--${tone}`,
      `privacy-note--spacing-${spacing}`,
    ]"
  >
    <text v-if="showMark" class="privacy-note__mark" aria-hidden="true">
      {{ mark }}
    </text>
    <text>{{ message }}</text>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.privacy-note {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  text-align: center;

  &--spacing-compact {
    margin-top: 20rpx;
  }

  &--spacing-normal {
    margin-top: 22rpx;
  }

  &--light {
    color: $color-muted;
    font-size: 20rpx;
  }

  &--dark {
    color: rgba($color-white, 0.52);
    font-size: 18rpx;
    line-height: 1.6;
  }

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

  &--dark &__mark {
    border-color: rgba($color-white, 0.28);
    color: rgba($color-white, 0.72);
  }
}
</style>
