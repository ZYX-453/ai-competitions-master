import type { AnswerEvent, Badge } from '../lib/types'

/** 结论区：整个回答的"帽子"。
 *
 * 设计取舍：把 headline 做成 16px 粗体、正文 12.5px，形成 1.9 的对比。
 * 大多数仪表盘把两者做成 14/13，看起来"信息很密"，实际是主次不分。
 * 企业级工具的观感来自**开合有度**：一眼能看到结论，细节退到后面。
 */
export function Verdict({ answer }: { answer: AnswerEvent }) {
  // 后端用 \n\n 分段（现象 / 拆解 / 排除 / 代价），这里拆成 <p>。
  // 密实长段是"专业工具"和"看起来专业的模板"之间最容易露出破绽的地方：
  // 真正给人扫读的界面，段落之间一定有呼吸。
  const paras = (answer.summary || '').split('\n').filter((s) => s.trim())

  return (
    <div className="verdict">
      <div className="verdict-head">
        <BadgeTag badge={answer.badge} />
        <h2 className="verdict-h">{answer.headline}</h2>
      </div>
      <div className="verdict-body">
        {paras.map((p, i) => (
          <p key={i} className="verdict-p" dangerouslySetInnerHTML={{ __html: emphasize(p) }} />
        ))}
      </div>
    </div>
  )
}

function BadgeTag({ badge }: { badge: Badge }) {
  return <span className={`badge ${badge.level}`}>{badge.label}</span>
}

/** 把总结里的**加粗**和数字转成可读强调。
 *
 * 后端用 markdown 风格的 ** 做强调；这里只处理这一种，
 * 不引 markdown 库——为了两个星号装 30KB 不值得。
 * 数字加等宽 class 是关键：让 18.09% 和 1, 2, 3 在视觉上对齐。
 */
function emphasize(s: string): string {
  if (!s) return ''
  let out = escapeHtml(s)
  out = out.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  // 数字高亮：百分数、金额、带小数的数
  out = out.replace(
    /(-?\d[\d,]*\.?\d*%|-?¥[\d,]+\.?\d*|-?\b\d[\d,]*\.\d+\b)/g,
    '<b>$1</b>',
  )
  return out
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}