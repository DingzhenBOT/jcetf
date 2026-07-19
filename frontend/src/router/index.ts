import { createRouter, createWebHashHistory } from 'vue-router'
import MarketOverview from '@/views/MarketOverview.vue'
import SectorView from '@/views/SectorView.vue'
import EtfList from '@/views/EtfList.vue'
import EtfDetail from '@/views/EtfDetail.vue'
import SystemStatus from '@/views/SystemStatus.vue'

// 使用 hash 历史：静态文件托管（Nginx）无需 try_files 回退配置，部署更稳健。
// API 调用走相对路径 /api，与路由模式无关。
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'overview', component: MarketOverview, meta: { title: '总览' } },
    { path: '/sectors', name: 'sectors', component: SectorView, meta: { title: '板块' } },
    { path: '/etfs', name: 'etfs', component: EtfList, meta: { title: 'ETF 列表' } },
    { path: '/etfs/:code', name: 'etf-detail', component: EtfDetail, meta: { title: 'ETF 详情' } },
    { path: '/system', name: 'system', component: SystemStatus, meta: { title: '系统状态' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})
