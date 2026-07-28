<script setup lang="ts">
type StateKind = "loading" | "error" | "empty" | "neutral";
type StateTone = "light" | "dark";
type StateSurface = "quiet" | "raised";
type StateDensity = "compact" | "regular" | "spacious";
type StateSpacing = "none" | "section" | "inset";

withDefaults(
  defineProps<{
    kind?: StateKind;
    tone?: StateTone;
    surface?: StateSurface;
    density?: StateDensity;
    spacing?: StateSpacing;
    mark?: string;
    title?: string;
    message: string;
    note?: string;
    actionLabel?: string;
    actionDisabled?: boolean;
  }>(),
  {
    kind: "neutral",
    tone: "light",
    surface: "quiet",
    density: "regular",
    spacing: "none",
    mark: undefined,
    title: undefined,
    note: undefined,
    actionLabel: undefined,
    actionDisabled: false,
  },
);

defineEmits<{
  action: [];
}>();
</script>

<template>
  <view
    class="state-card"
    :class="[
      `state-card--${kind}`,
      `state-card--${tone}`,
      `state-card--${surface}`,
      `state-card--${density}`,
      `state-card--spacing-${spacing}`,
    ]"
    :role="kind === 'error' ? 'alert' : 'status'"
    :aria-live="kind === 'error' ? 'assertive' : 'polite'"
  >
    <text v-if="mark" class="state-card__mark">{{ mark }}</text>
    <text v-if="title" class="state-card__title">{{ title }}</text>
    <text class="state-card__message">{{ message }}</text>
    <text v-if="note" class="state-card__note">{{ note }}</text>
    <button
      v-if="actionLabel"
      class="state-card__action"
      :disabled="actionDisabled"
      @click="$emit('action')"
    >
      {{ actionLabel }}
    </button>
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.state-card {
  display: grid;
  gap: 20rpx;
  justify-items: center;
  border: 1rpx solid;
  border-radius: $radius-large;
  text-align: center;

  &--compact {
    padding: 48rpx 32rpx;
  }

  &--regular {
    padding: 60rpx 32rpx;
  }

  &--spacious {
    padding: 72rpx 32rpx;
  }

  &--spacing-section {
    margin-top: 48rpx;
  }

  &--spacing-inset {
    margin: 48rpx 32rpx;
  }

  &--light {
    border-color: rgba($color-ink, 0.1);
    color: $color-muted;
  }

  &--dark {
    border-color: rgba($color-white, 0.14);
    color: rgba($color-white, 0.72);
  }

  &--raised {
    background: rgba($color-white, 0.94);
    box-shadow: $shadow-soft;
  }

  &--empty {
    border-style: dashed;
  }

  &__mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 70rpx;
    height: 70rpx;
    border-radius: 50%;
    background: $color-paper-deep;
    color: $color-vermilion;
    font-family: "Songti SC", "STSong", serif;
    font-size: 32rpx;
  }

  &__title,
  &__message,
  &__note {
    display: block;
  }

  &__title {
    color: inherit;
    font-family: "Songti SC", "STSong", serif;
    font-size: 31rpx;
    font-weight: 700;
    line-height: 1.45;
  }

  &__message {
    font-size: 23rpx;
    line-height: 1.65;
  }

  &__note {
    color: inherit;
    font-size: 20rpx;
    line-height: 1.6;
    opacity: 0.82;
  }

  &__action {
    min-width: 220rpx;
    height: 88rpx;
    margin-top: 2rpx;
    border-radius: 999rpx;
    color: $color-white;
    font-size: 23rpx;
    line-height: 88rpx;

    &::after {
      border: 0;
    }
  }

  &--empty &__mark {
    color: $color-sage-deep;
  }

  &--light &__action {
    background: $color-ink;
  }

  &--dark &__action {
    background: $color-vermilion;
  }
}
</style>
