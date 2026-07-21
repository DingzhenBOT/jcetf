import { createRouter, createWebHashHistory } from 'vue-router'
import MarketOverview from '@/views/MarketOverview.vue'
import SectorView from '@/views/SectorView.vue'
import EtfList from '@/views/EtfList.vue'
import EtfDetail from '@/views/EtfDetail.vue'
import SystemStatus from '@/views/SystemStatus.vue'
import PortfolioView from '@/views/PortfolioView.vue'

// 使用 hash 历史：静态文件托管（Nginx）无需 try_files 回退配置，部署更稳健。
// API 调用走相对路径 /api，与路由模式无关。
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'overview', component: MarketOverview, meta: { title: '总览' } },
    { path: '/sectors', name: 'sectors', component: SectorView, meta: { title: '板块' } },
    { path: '/etfs', name: 'etfs', component: EtfList, meta: { title: 'ETF 列表' } },
    { path: '/etfs/:code', name: 'etf-detail', component: EtfDetail, meta: { title: 'ETF 详情' } },
    { path: '/portfolio', name: 'portfolio', component: PortfolioView, meta: { title: '持仓分析' } },
    { path: '/system', name: 'system', component: SystemStatus, meta: { title: '系统状态' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  // 仅在「前进/新页面」时置顶；浏览器前进/后退（savedPosition）时还原原滚动位置，
  // 避免用户在长页面（如下方信号表）被无谓拉回顶部。
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
})
