<script setup lang="ts">
import { watch, onBeforeUnmount } from 'vue'

// 通用模态框：遮罩 + 居中卡片 + 关闭按钮。点遮罩或按 Esc 关闭。
const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
  }>(),
  { title: '' },
)
const emit = defineEmits<{ (e: 'close'): void }>()

function close(): void {
  emit('close')
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') close()
}

watch(
  () => props.open,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm"
        @click.self="close"
      >
        <div
          class="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl bg-white shadow-xl border border-slate-200"
          role="dialog"
          aria-modal="true"
        >
          <div
            class="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-slate-100 sticky top-0 bg-white"
          >
            <h3 class="text-sm font-semibold text-slate-800 truncate">{{ title }}</h3>
            <button
              type="button"
              class="shrink-0 rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition"
              aria-label="关闭"
              @click="close"
            >
              <svg viewBox="0 0 20 20" class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M5 5l10 10M15 5L5 15" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <div class="px-5 py-4 text-sm leading-relaxed text-slate-700">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.18s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
</style>
