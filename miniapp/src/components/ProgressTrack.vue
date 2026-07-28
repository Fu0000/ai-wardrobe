<script setup lang="ts">
import { computed } from "vue";

type ProgressSize = "compact" | "standard" | "prominent";
type ProgressTone = "calm" | "contrast";
type ProgressSpacing = "none" | "normal" | "loose";

const props = withDefaults(
  defineProps<{
    value: number;
    label: string;
    size?: ProgressSize;
    tone?: ProgressTone;
    spacing?: ProgressSpacing;
  }>(),
  {
    size: "standard",
    tone: "calm",
    spacing: "normal",
  },
);

const normalizedValue = computed(() =>
  Math.min(100, Math.max(0, Math.round(props.value))),
);
</script>

<template>
  <view
    class="progress-track"
    :class="[
      `progress-track--${size}`,
      `progress-track--${tone}`,
      `progress-track--spacing-${spacing}`,
    ]"
    role="progressbar"
    :aria-label="label"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-valuenow="normalizedValue"
  >
    <view
      class="progress-track__fill"
      :style="{ width: `${normalizedValue}%` }"
    />
  </view>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.progress-track {
  width: 100%;
  overflow: hidden;
  border-radius: 999rpx;
  background: $color-paper-deep;

  &--compact {
    height: 8rpx;
  }

  &--standard {
    height: 9rpx;
  }

  &--prominent {
    height: 12rpx;
  }

  &--spacing-normal {
    margin-top: 28rpx;
  }

  &--spacing-loose {
    margin-top: 32rpx;
  }

  &__fill {
    height: 100%;
    border-radius: inherit;
    transition: width 360ms ease;
  }

  &--calm &__fill {
    background: linear-gradient(90deg, $color-sage, $color-vermilion);
  }

  &--contrast &__fill {
    background: linear-gradient(90deg, $color-sage-deep, $color-vermilion);
  }
}
</style>
