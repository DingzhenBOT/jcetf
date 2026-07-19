/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      fontFamily: {
        // 禁用 Inter（frontend-dev 规范）；中文环境用系统无衬线 + 中文回退。
        sans: [
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'sans-serif',
        ],
      },
      colors: {
        // 金融语义色（A股惯例：红涨绿跌）。非「AI 紫/蓝」高危饱和色。
        up: '#dc2626', // 涨 = 红
        down: '#16a34a', // 跌 = 绿
        flat: '#64748b', // 平 = 灰
      },
    },
  },
  plugins: [],
}
