<script setup lang="ts">
defineProps<{
  title?: string
  subtitle?: string
}>()
</script>

<template>
  <section class="bg-white border border-slate-200 rounded-xl shadow-sm">
    <header
      v-if="title || $slots.actions"
      class="flex items-center justify-between gap-3 px-4 py-3 border-b border-slate-100"
    >
      <div class="min-w-0">
        <h2 class="text-sm font-semibold text-slate-700 truncate">{{ title }}</h2>
        <p v-if="subtitle" class="text-xs text-slate-400 mt-0.5 truncate">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="flex items-center gap-2 shrink-0">
        <slot name="actions" />
      </div>
    </header>
    <div class="p-4">
      <slot />
    </div>
  </section>
</template>
