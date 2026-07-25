// 资讯影响分析（规则模板生成，离线、无需 LLM）。
//
// 思路：用关键词命中「关联板块」与「情绪方向（利好/利空/中性）」，再拼装成
// 人话模板，给出对板块/大盘的定性影响。命中规则透明可查，便于后续维护与交接。
//
// 注：这是轻量启发式，非投资建议；仅用于在资讯弹窗里给值班同学一个快速提示。

export type Sentiment = '利好' | '利空' | '中性'

export interface NewsImpact {
  sentiment: Sentiment
  sectors: string[] // 命中的关联板块
  text: string // 一句话影响分析
  matched: string[] // 命中的关键词（透明可查）
}

// 板块 -> 触发词（命中其一即认为相关）
const SECTOR_RULES: Record<string, string[]> = {
  半导体: ['半导体', '芯片', '集成电路', '晶圆', '光刻', '英伟达', '寒武纪'],
  新能源: ['新能源', '光伏', '锂电', '储能', '电动车', '新能源车', '电池', '风电'],
  消费: ['白酒', '消费', '零售', '食品', '饮料', '家电', '商超'],
  金融: ['券商', '银行', '保险', '金融', '信贷', '信托', '公募', '私募'],
  地产: ['房地产', '地产', '楼市', '房企', '棚改', '保障房'],
  周期资源: ['钢铁', '有色', '煤炭', '水泥', '化工', '稀土', '铜', '铝', '锂矿'],
  医药: ['医药', '医疗', '创新药', '生物', '疫苗', '集采', 'CXO'],
  军工: ['军工', '国防', '航空', '航天', '兵器'],
  汽车: ['汽车', '整车', '车企', '比亚迪', '蔚来', '理想', '小鹏'],
  农业: ['农业', '种业', '猪肉', '粮食', '养殖'],
  传媒: ['传媒', '游戏', '影视', '直播', '出版'],
  科技TMT: ['通信', '5G', '算力', '人工智能', 'AI', '大模型', '数据中心', '服务器'],
  基建: ['基建', '水利', '高铁', '电网', '特高压', '专项债'],
}

// 情绪方向 -> 触发词
const SENTIMENT_POSITIVE = [
  '利好', '上涨', '涨停', '提振', '超预期', '降准', '降息', '放水', '宽松',
  '回购', '增持', '中标', '提价', '复苏', '回暖', '增长', '扭亏', '扩产',
  '合作', '签约', '获批', '突破',
]
const SENTIMENT_NEGATIVE = [
  '利空', '下跌', '暴跌', '跌停', '收紧', '加息', '处罚', '调查', '立案',
  '减持', '退市', '亏损', '爆雷', '风险', '警示', '约谈', '下修', '下调',
  '违约', '冻结', '骗', '造假',
]

function matchKeywords(text: string, words: string[]): string[] {
  const hit = words.filter((w) => text.includes(w))
  return hit
}

export function analyzeNewsImpact(title: string, summary = ''): NewsImpact {
  const text = `${title}\n${summary}`

  const sectors: string[] = []
  const sectorHits: string[] = []
  for (const [sector, words] of Object.entries(SECTOR_RULES)) {
    const hit = matchKeywords(text, words)
    if (hit.length) {
      sectors.push(sector)
      sectorHits.push(...hit)
    }
  }

  const pos = matchKeywords(text, SENTIMENT_POSITIVE)
  const neg = matchKeywords(text, SENTIMENT_NEGATIVE)

  let sentiment: Sentiment = '中性'
  if (pos.length > neg.length) sentiment = '利好'
  else if (neg.length > pos.length) sentiment = '利空'

  const matched = [...new Set([...sectorHits, ...pos, ...neg])]

  let body: string
  const sectorPhrase = sectors.length ? `【${sectors.join('、')}】等板块` : '大盘整体'
  if (sentiment === '利好') {
    body = `偏利好，或带动${sectorPhrase}上修预期，对市场风险偏好形成正面拉动。`
  } else if (sentiment === '利空') {
    body = `偏利空，或对${sectorPhrase}形成短期压力，压制市场风险偏好，注意回撤。`
  } else {
    body = sectors.length
      ? `暂未识别到明确多空倾向，但涉及${sectorPhrase}，建议结合盘面观察资金反应。`
      : `暂未识别到明确板块指向与多空倾向，建议结合盘面与量能观察。`
  }

  return {
    sentiment,
    sectors,
    matched,
    text: `${body}（仅供参考，非投资建议）`,
  }
}
