/* ===========================================================================
 * 页面共用件
 *
 * 【为什么这些组件要做成"无状态的一块"】
 * 六个功能页面的骨架高度相似：一个页头、若干张卡片、卡里一张表或一张图。
 * 如果每页各写一遍，最先分叉的不是样式，而是**口径说明的位置**——
 * 有的页把"这个数怎么算的"写在标题旁，有的塞进脚注，有的干脆不写。
 * 而口径说明是这套系统最不能省的文本：没有它，一个数字就只是
 * 一个看起来很专业的断言。
 *
 * 所以统一成这样：PageHead 负责"这页在回答什么问题"，
 * Card 负责"这块数据是什么口径"。位置固定，读者不用找。
 * =========================================================================== */
import type { ReactNode } from 'react'

/** 页面头。标题 + 一句话 + 右侧的操作区（筛选器之类）。 */
export function PageHead({
  eyebrow, title, desc, right,
}: { eyebrow?: string; title: string; desc?: ReactNode; right?: ReactNode }) {
  return (
    <div className="pg-head">
      <div className="pg-head-main">
        {eyebrow && <div className="pg-eyebrow">{eyebrow}</div>}
        <h1 className="pg-title">{title}</h1>
        {/* 【描述里允许放 JSX，因为多数描述要强调某个词】
            纯字符串就得用 title 属性或者拆成几段，读起来会散。 */}
        {desc && <p className="pg-desc">{desc}</p>}
      </div>
      {right && <div className="pg-head-right">{right}</div>}
    </div>
  )
}

/** 卡片。note 是口径说明，固定出现在标题右侧，不折叠。 */
export function Card({
  title, note, right, children, pad = true, tone, className,
}: {
  title?: ReactNode
  note?: ReactNode
  right?: ReactNode
  children: ReactNode
  pad?: boolean
  tone?: 'bad' | 'warn' | 'good' | 'info'
  className?: string
}) {
  return (
    <section className={`pg-card${tone ? ` tone-${tone}` : ''}${className ? ` ${className}` : ''}`}>
      {(title || right) && (
        <header className="pg-card-h">
          {title && <h2 className="pg-card-t">{title}</h2>}
          {/* 【note 不折叠、不塞脚注】
              折叠起来的口径说明等于没有：读者默认不会点开，
              于是会按自己的理解去读那个数字。 */}
          {note && <div className="pg-card-note">{note}</div>}
          {right && <div className="pg-card-right">{right}</div>}
        </header>
      )}
      <div className={pad ? 'pg-card-b' : ''}>{children}</div>
    </section>
  )
}

/** 大数字格。trend 只表达方向，不表达大小。 */
export function Stat({
  label, value, unit, sub, tone, title,
}: {
  label: string
  value: ReactNode
  unit?: string
  sub?: ReactNode
  tone?: 'bad' | 'warn' | 'good' | 'info'
  title?: string
}) {
  return (
    <div className="pg-stat" title={title}>
      <div className="pg-stat-k">{label}</div>
      <div className={`pg-stat-v${tone ? ` t-${tone}` : ''}`}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {sub && <div className="pg-stat-s">{sub}</div>}
    </div>
  )
}

/** 空态。**必须说清"为什么空"**，不能只写"暂无数据"。 */
export function Empty({ what, why }: { what: string; why?: string }) {
  return (
    <div className="pg-empty">
      <div className="pg-empty-w">{what}</div>
      {why && <div className="pg-empty-y">{why}</div>}
    </div>
  )
}

/** 加载骨架。不做花哨动画，只表示"这里会有东西"。 */
export function Loading({ label = '加载中' }: { label?: string }) {
  return (
    <div className="pg-loading">
      <div className="spinner" />
      <span>{label}</span>
    </div>
  )
}

/** 错误块。**把错误原文显示出来**，不吞。 */
export function Fail({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="pg-fail">
      <div className="pg-fail-h">这一页没加载出来</div>
      <div className="pg-fail-m">{msg}</div>
      {onRetry && <button className="pg-btn" onClick={onRetry}>重试</button>}
    </div>
  )
}

/** 竖排条形。用于"排行"类数据，比横向条形更省纵向空间。 */
export function Bar({
  pct, tone = 'accent', height = 6,
}: { pct: number; tone?: 'accent' | 'bad' | 'warn' | 'info'; height?: number }) {
  return (
    <div className="pg-bar" style={{ height }}>
      <div
        className={`pg-bar-f t-${tone}`}
        // 【宽度夹在 0–100 之间】
        // 上游偶尔会因为口径差异给出略大于 100 的值（比如
        // "占该类目比例"在浮点累加后成了 100.0000001），
        // 不夹的话那一条会捅出容器。
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
      />
    </div>
  )
}

/** 格式化：千分位。所有大数字都过这个，避免各页各写一遍。 */
export function nf(n: number, digits = 0): string {
  if (!isFinite(n)) return '—'
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 金额：自动切到"万"，让一屏能容下。 */
export function money(n: number): { v: string; u: string } {
  if (!isFinite(n)) return { v: '—', u: '' }
  if (Math.abs(n) >= 1e8) return { v: (n / 1e8).toFixed(2), u: '亿' }
  if (Math.abs(n) >= 1e4) return { v: (n / 1e4).toFixed(1), u: '万' }
  return { v: nf(n), u: '' }
}

/** 带符号的 pp。异常页里到处都要显示"偏离了多少"，统一处理正负号。 */
export function pp(n: number, digits = 2): string {
  if (!isFinite(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`
}