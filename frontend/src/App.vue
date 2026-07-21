<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import AppNav from '@/components/ui/AppNav.vue'
import { startPolling, stopPolling } from '@/stores/market'

// 启动全局轮询（总览 + 最新信号，默认 60s 间隔）；卸载时停止，避免重复计时器。
onMounted(() => startPolling())
onUnmounted(() => stopPolling())
</script>

<template>
  <div class="min-h-[100dvh] flex flex-col">
    <AppNav />
    <main class="flex-1 w-full max-w-[1400px] mx-auto px-4 py-6">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>
    <footer class="border-t border-slate-200 py-4 text-center text-xs text-slate-400">
      内部工具 · 数据由后端确定性规则引擎生成，仅供研究参考，不构成投资建议
    </footer>
  </div>
</template>
