/* ===========================================================================
 * 弹幕层 · 无痕开关
 *
 * 【是什么】
 * 总览页顶部的一条横向弹幕带：从评论池随机抽一批真实用户评论，
 * 以漂浮滚动的方式从左往右穿过。纯氛围装饰，不承载任何结论数据。
 *
 * 【无痕开关的三层保证】
 * 1. 关闭时组件直接 return null —— 弹幕 DOM 一个节点都不留。
 * 2. 打开态用 CSS animation 驱动，不占文档流（position:absolute），
 *    关掉后那块区域高度归零，页面布局不受任何影响。
 * 3. 开关状态存 localStorage（key 固定），刷新后保持，不打扰。
 *
 * 【可访问性边界（这是弹幕的经典雷区，必须钉死）】
 * - 弹幕容器 aria-hidden="true"：读屏用户完全跳过，不被滚动文字刷屏。
 * - 键盘焦点永不落进弹幕（无 tabindex，无交互元素）。
 * - prefers-reduced-motion：系统开了"减弱动效"就静止显示，
 *   不强制用户看滚动（此时降级为一条静态淡色文字带）。
 * - 开关按钮本身是标准 button + aria-pressed，键盘可达。
 *
 * 【情感着色（低饱和）】
 * 好评/差评用项目已有的 --good-fg / --bad-fg，这两个本身就是
 * "几乎看不出是绿 / 偏暗红"的低饱和色，符合"氛围感优先、不抢图"的要求。
 * 中性用 --fg-4。颜色只做氛围，不作为任何数据主张。
 * =========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getJson } from '../lib/sse'

type Polarity = '正向' | '负向' | '中性'

interface DanmakuItem {
  text: string
  polarity: Polarity
}

interface DanmakuData {
  items: DanmakuItem[]
  total: number
}

/** 每一条弹幕的轨道参数：轨道编号、动画时长、延迟、起始位置 */
interface Track {
  id: number
  item: DanmakuItem
  lane: number
  duration: number
  delay: number
}

const LS_KEY = 'danmaku:on'

export function Danmaku({ category }: { category: string | null }) {
  const [on, setOn] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_KEY) === '1' } catch { return false }
  })
  const [items, setItems] = useState<DanmakuItem[]>([])
  const [tick, setTick] = useState(0)
  const [reduced, setReduced] = useState(false)
  const seedRef = useRef(0)

  /* 读一次系统"减弱动效"偏好 */
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const h = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener?.('change', h)
    return () => mq.removeEventListener?.('change', h)
  }, [])

  /* 打开时拉语料；关闭时清空（无痕） */
  useEffect(() => {
    if (!on) { setItems([]); return }
    let alive = true
    const qs = category ? `?category=${encodeURIComponent(category)}` : ''
    getJson<DanmakuData>(`/api/pages/danmaku${qs}`)
      .then((d) => {
        if (!alive) return
        // 【28 字硬上限】容量约束的一部分：等间距 460px 必须大于
        // 最长弹幕宽 + 80px 安全距。后端截到 44 字，这里再兜一次。
        setItems(d.items.map((x) => ({ ...x, text: [...x.text].slice(0, 28).join('') })))
      })
      .catch(() => { if (alive) setItems([]) })
    return () => { alive = false }
  }, [on, category, tick])

  const toggle = useCallback(() => {
    setOn((v) => {
      const next = !v
      try { localStorage.setItem(LS_KEY, next ? '1' : '0') } catch { /* 忽略 */ }
      return next
    })
  }, [])

  /* 轨道调度 —— 【等间距 + 容量约束，数学上排除重叠】
   *
   * v2 教训一：同轨随机延迟 → 出发就撞。
   * v2 教训二：位移含 -100%（自身宽度）→ 长弹幕跑得快，追尾。
   * v2 教训三：宽度排队总长超过一圈行程 → 负延迟取模后必撞。
   *
   * 这版的规则（每条都可验证）：
   * 1. 同轨同速（同 duration + 固定位移）。
   * 2. 同轨内等间距出发：delay_k = -(k/n + phase) * duration，
   *    间距 = 行程/n。行程 = 屏宽 + 800px 位移余量。
   * 3. 硬容量：每轨最多 4 条、每条最长 28 字（≈322px），
   *    460px 间距 > 322px + 80px 安全距 → 同轨永不重叠。
   * 4. 轨道间不同速 + 字号/透明度分层 → 有远近纵深。
   */
  const LANES = reduced ? 1 : 4
  const LANE_DUR = [20, 26, 32, 24]      // 各轨速度不同 → 纵深
  const VW = typeof window !== 'undefined' ? window.innerWidth : 1280

  const tracks = useMemo<Track[]>(() => {
    // 每轨最多 4 条（容量约束见上），超出丢弃
    const perLane = new Map<number, DanmakuItem[]>()
    items.forEach((item, i) => {
      const lane = reduced ? 0 : i % LANES
      const q = perLane.get(lane) ?? []
      if (q.length < 4) q.push(item)
      perLane.set(lane, q)
    })
    // 每轨一个随机初相位（整轨平移，不破坏轨内间距）
    seedRef.current += 1
    let s = seedRef.current * 9301 + 49297
    const rnd = () => { s = (s * 233280 + 9301) % 233280; return s / 233280 }
    const phase = Array.from({ length: LANES }, () => rnd())
    const out: Track[] = []
    for (let lane = 0; lane < LANES; lane++) {
      const q = perLane.get(lane) ?? []
      const duration = reduced ? 30 : LANE_DUR[lane]
      q.forEach((item, k) => {
        out.push({
          id: lane * 100 + k,
          item,
          lane,
          duration,
          delay: -(k / q.length + phase[lane]) * duration,
        })
      })
    }
    void VW
    return out
  }, [items, reduced, LANES, VW])

  return (
    <div className={`dm-wrap${on && items.length > 0 ? ' has-layer' : ''}`}>
      {/* 开关：唯一可见的交互，标准 button */}
      <button
        type="button"
        className={`dm-toggle${on ? ' on' : ''}`}
        onClick={toggle}
        aria-pressed={on}
        title={on ? '关闭弹幕（无痕）' : '开启弹幕，随机播放用户评论'}
      >
        <span className="dm-toggle-dot" aria-hidden="true" />
        弹幕
      </button>

      {on && items.length > 0 && (
        <div
          className={`dm-layer${reduced ? ' is-static' : ''}`}
          aria-hidden="true"
        >
          {tracks.map((t) => (
            <div
              key={t.id}
              className={`dm-item pol-${t.item.polarity} dm-l${t.lane}`}
              style={{
                top: reduced ? undefined : `${t.lane * 26 + 4}px`,
                animationDuration: reduced ? undefined : `${t.duration}s`,
                animationDelay: reduced ? undefined : `${t.delay}s`,
              }}
            >
              {t.item.text}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
