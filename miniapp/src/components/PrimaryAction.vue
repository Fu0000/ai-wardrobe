<script setup lang="ts">
type PrimaryActionTone = "ink" | "accent";
type PrimaryActionSize = "regular" | "large";
type PrimaryActionSpacing = "tight" | "normal" | "loose";

withDefaults(
  defineProps<{
    label: string;
    disabled?: boolean;
    tone?: PrimaryActionTone;
    size?: PrimaryActionSize;
    spacing?: PrimaryActionSpacing;
    showArrow?: boolean;
  }>(),
  {
    disabled: false,
    tone: "ink",
    size: "large",
    spacing: "normal",
    showArrow: true,
  },
);

defineEmits<{
  action: [];
}>();
</script>

<template>
  <button
    class="primary-action"
    :class="[
      `primary-action--${tone}`,
      `primary-action--${size}`,
      `primary-action--spacing-${spacing}`,
    ]"
    :disabled="disabled"
    @click="$emit('action')"
  >
    <text>{{ label }}</text>
    <text v-if="showArrow" class="primary-action__arrow" aria-hidden="true">
      ↗
    </text>
  </button>
</template>

<style lang="scss" scoped>
@use "@/styles/tokens.scss" as *;

.primary-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0 34rpx;
  border-radius: 999rpx;
  color: $color-white;
  font-weight: 700;

  &::after {
    border: 0;
  }

  &[disabled] {
    opacity: 0.58;
  }

  &--regular {
    height: 96rpx;
    font-size: 26rpx;
    line-height: 96rpx;
  }

  &--large {
    height: 104rpx;
    font-size: 28rpx;
    line-height: 104rpx;
  }

  &--spacing-tight {
    margin-top: 24rpx;
  }

  &--spacing-normal {
    margin-top: 28rpx;
  }

  &--spacing-loose {
    margin-top: 32rpx;
  }

  &--ink {
    background: $color-ink;
  }

  &--accent {
    background: $color-vermilion;
  }

  &__arrow {
    color: $color-white;
    font-family: "Songti SC", serif;
    font-size: 40rpx;
  }

  &--ink &__arrow {
    color: $color-vermilion;
    font-size: 42rpx;
  }
}
</style>
