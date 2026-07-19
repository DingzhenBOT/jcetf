<script setup lang="ts">
// 仅提供形状（圆角/边框/字号）；配色由父级通过 class 传入（完整 bg/text/border），
// 避免与默认色类名冲突导致颜色错乱。
defineProps<{ text: string }>()
</script>

<template>
  <span
    class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border border-transparent"
  >
    <slot>{{ text }}</slot>
  </span>
</template>
