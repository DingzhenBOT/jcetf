<script setup lang="ts">
// Loading / Empty / Error 三态面板（frontend-dev 质量门强制项）。
// 默认插槽：数据就绪且非空时渲染。
defineProps<{
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyText?: string
  loadingText?: string
}>()
const emit = defineEmits<{ (e: 'retry'): void }>()
</script>

<template>
  <div>
    <!-- 加载中：骨架 + 提示 -->
    <div v-if="loading" class="py-10" aria-live="polite">
      <div class="flex flex-col items-center gap-3 text-slate-400">
        <span
          class="w-6 h-6 rounded-full border-2 border-slate-200 border-t-slate-400 animate-spin"
          aria-hidden="true"
        />
        <span class="text-sm">{{ loadingText ?? '加载中…' }}</span>
      </div>
      <div class="mt-4 space-y-2">
        <div v-for="i in 3" :key="i" class="h-4 rounded bg-slate-100 animate-pulse" />
      </div>
    </div>

    <!-- 错误 -->
    <div
      v-else-if="error"
      class="py-10 flex flex-col items-center gap-3 text-center"
      role="alert"
    >
      <span class="text-sm font-medium text-rose-600">加载失败</span>
      <span class="text-xs text-slate-400 max-w-md break-all">{{ error }}</span>
      <button
        class="mt-1 px-3 py-1.5 text-sm rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100 transition active:scale-[0.98]"
        @click="emit('retry')"
      >
        重试
      </button>
    </div>

    <!-- 空 -->
    <div v-else-if="empty" class="py-10 text-center text-sm text-slate-400">
      {{ emptyText ?? '暂无数据' }}
    </div>

    <!-- 正常内容 -->
    <slot v-else />
  </div>
</template>
