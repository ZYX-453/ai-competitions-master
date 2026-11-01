/* ===========================================================================
 * 应用外壳：多页面工作台
 *
 * 【为什么改成多页面，而不是在一个问答页上堆功能】
 * 这两件事的交互形态天生不同：
 *   · 问答要"按需深入"——用户不知道答案长什么样，所以让 Agent 自己决定查什么
 *   · 页面要"一屏看全"——用户知道自己想看什么，要求的是一次看够、能横向比
 * 硬塞进一个界面，结果是两边都别扭：页面上出现一堆下拉框让用户自己拼查询，
 * 或者问答里被迫先铺一张总表再开始回答。
 *
 * 【为什么不用 react-router】
 * 当前依赖只有 react/react-dom。加一个路由库在 npm 装不稳定的环境里
 * 是个不必要的风险，而这里需要的只是"记住当前在哪一页 + 能前进后退"。
 * 用 hash 就够了，而且刷新、分享链接、浏览器后退全都天然可用。
 * 代价是参数只能放 hash 里（?week=44 要写成 #/attribution/44），
 * 但这套页面的状态本来就应该可分享——"第 44 周归因页"是一个有意义的链接。
 *
 * 【为什么页面状态（周次/类目/实体）提到 App 层】
 * 从总览点某个异常周跳归因页，归因页得知道是哪一周。
 * 如果状态留在各页内部，这个跳转就得靠 URL 传参再解析一遍；
 * 提到上层后，跳转就是一次 setState，而且切换页面时选择不会丢
 * （看完归因回到总览，还是原来那些筛选条件）。
 * =========================================================================== */
import { useCallback, useEffect, useState } from 'react'
import { getJson } from './lib/sse'
import type { NavData } from './lib/pages'
import { Dashboard } from './pages/Dashboard'
import { Attribution } from './pages/Attribution'
import { Reasons } from './pages/Reasons'
import { Reviews } from './pages/Reviews'
import { Competitors } from './pages/Competitors'
import { EvalPage } from './pages/EvalPage'
import { ChatPage } from './pages/ChatPage'
import { Loading } from './components/PageKit'

interface Health {
  ok: boolean
  mode: string
  model: string
  llm_base: string | null
  data: {
    skus: number; weeks: number; latest_week: number; categories: string[]
    orders_year: number
    return_rate: number
    rows: Record<string, number>
  }
  tools: string[]
}

type PageKey = 'dashboard' | 'attribution' | 'reasons' | 'reviews' | 'competitors' | 'eval' | 'chat'

const VALID: PageKey[] = ['dashboard', 'attribution', 'reasons', 'reviews', 'competitors', 'eval', 'chat']

/** 解析 hash：#/attribution/44 → { page: 'attribution', arg: '44' } */
function parseHash(): { page: PageKey; arg: string } {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [p, a] = raw.split('/')
  const page = (VALID as string[]).includes(p) ? (p as PageKey) : 'dashboard'
  return { page, arg: a ?? '' }
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [nav, setNav] = useState<NavData | null>(null)
  const [page, setPage] = useState<PageKey>(() => parseHash().page)

  // 跨页面共享的筛选状态
  //
  // 【week 的初值为什么是 0 而不是某个具体周】
  // 早先这里写死 44。写死的坏处不是"可能不准确"，是它会**盖住 hash**：
  // 打开 #/attribution/30 时，如果 nav 还没回来，页面先按 44 渲染一次，
  // 然后才被纠正 —— 用户看到的是一次闪烁 + 一次重新取数。
  // 0 在这里是明确的"还没确定"，下面会区分三种来源：
  //   hash 指定 > 后端建议 > 最新一周
  const [week, setWeek] = useState(0)
  const [category, setCategory] = useState('')
  const [entity, setEntity] = useState('本店')

  useEffect(() => {
    getJson<Health>('/api/health').then(setHealth).catch(() => setHealth(null))
    // 【hash 里写明的周次优先于一切】
    // 踩过的坑：#/attribution/44 打开显示的是第 52 周。
    // 原因是 nav 回调里无条件 setWeek(latest_week)，把 hash 解析出的 44 吃掉了。
    // 而"第 44 周归因页"是一个有意义的可分享链接，覆盖它等于链接失效。
    const hashWeek = (() => {
      const { page: p, arg } = parseHash()
      return p === 'attribution' && arg && /^\d+$/.test(arg) ? Number(arg) : 0
    })()
    getJson<NavData>('/api/pages/nav').then((n) => {
      setNav(n)
      setWeek((cur) => {
        if (hashWeek) return hashWeek         // hash 明确指定 → 听 hash
        if (cur) return cur                    // 用户已经翻过周 → 不打断
        // 都没指定 → 用后端挑的"最值得解释的那一周"
        // （后端用和判定同一套 _diagnose_all 选，不是随便取 latest）
        return n.default_week || n.latest_week
      })
    }).catch(() => setNav(null))
  }, [])

  // hash ←→ state 双向同步
  useEffect(() => {
    const onHash = () => {
      const { page: p, arg } = parseHash()
      setPage(p)
      // #/attribution/44 里的 44 是周次
      if (p === 'attribution' && arg && /^\d+$/.test(arg)) setWeek(Number(arg))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = useCallback((p: PageKey, arg?: string | number) => {
    const h = arg != null ? `#/${p}/${arg}` : `#/${p}`
    if (window.location.hash === h) {
      // 【hash 没变时手动触发一次状态更新】
      // hashchange 事件不会因为"设了同样的值"而触发，
      // 于是"在归因页点 W44，而当前已经在 #/attribution/44"会没有任何反应。
      // 手动 set 一次，保证点击永远有反馈。
      setPage(p)
      if (p === 'attribution' && arg != null) setWeek(Number(arg))
    } else {
      window.location.hash = h
    }
  }, [])

  const openWeek = useCallback((w: number) => {
    setWeek(w)
    go('attribution', w)
  }, [go])

  if (!nav) return <div className="pg-boot"><Loading label="连接后端" /></div>

  const cur = nav.pages.find((p) => p.key === page)
  const isChat = page === 'chat'

  return (
    <div className={`app${isChat ? ' app-chat' : ''}`}>
      <TopBar health={health} />

      <nav className="sidenav">
        <div className="sidenav-brand">
          <div className="brand-mark">炼</div>
          <div className="sidenav-brand-txt">
            <div className="brand-name">差评炼金炉</div>
            <div className="brand-sub">归因诊断工作台</div>
          </div>
        </div>

        <div className="sidenav-list">
          {nav.pages.map((p) => (
            <button
              key={p.key}
              className={`sidenav-item${page === p.key ? ' on' : ''}`}
              onClick={() => go(p.key as PageKey)}
              title={p.subtitle}
            >
              <NavIcon name={p.icon} />
              <span className="sidenav-txt">
                <span className="sidenav-t">{p.title}</span>
                <span className="sidenav-s">{p.subtitle}</span>
              </span>
            </button>
          ))}
        </div>

        <div className="sidenav-foot">
          {health ? (
            <>
              <div className="sidenav-meta">
                {health.data.skus} SKU · {health.data.weeks} 周
              </div>
              <div className="sidenav-meta dim">
                {Object.values(health.data.rows).reduce((a, b) => a + b, 0).toLocaleString()} 行数据
              </div>
            </>
          ) : (
            <div className="sidenav-meta dim">连接中…</div>
          )}
        </div>
      </nav>

      <main className="app-main">
        <header className="pagehead">
          <div className="pagehead-t">
            {cur?.title}
            <span className="pagehead-s">{cur?.subtitle}</span>
          </div>
          <div className="pagehead-r">
            {health && (
              <span className={`chip ${health.mode === 'llm' ? 'live' : ''}`}>
                <span className="dot" />
                {health.mode === 'llm' ? health.model : '确定性推理引擎'}
              </span>
            )}
            {page !== 'chat' && (
              <button className="pg-btn ghost" onClick={() => go('chat')}>
                去问一句 ›
              </button>
            )}
          </div>
        </header>

        <div className={`app-scroll${isChat ? ' no-pad' : ''}`}>
          {page === 'dashboard' && <Dashboard onOpenWeek={openWeek} />}
          {page === 'attribution' && (
            <Attribution week={week} setWeek={(w) => { setWeek(w); go('attribution', w) }} />
          )}
          {page === 'reasons' && (
            <Reasons
              categories={nav.categories}
              category={category}
              setCategory={setCategory}
            />
          )}
          {page === 'reviews' && (
            <Reviews entities={nav.entities} entity={entity} setEntity={setEntity} />
          )}
          {page === 'competitors' && (
            <Competitors
              categories={nav.categories}
              category={category}
              setCategory={setCategory}
            />
          )}
          {page === 'eval' && <EvalPage />}
          {page === 'chat' && <ChatPage health={health} nav={nav} />}
        </div>
      </main>
    </div>
  )
}

/* ---------------------------------------------------------------- 顶栏 */
function TopBar({ health }: { health: Health | null }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-name">差评炼金炉</span>
        <span className="brand-sep">/</span>
        <span className="brand-sub">把「这周为什么涨了」变成一条带证据的归因链</span>
      </div>
      <div className="top-spacer" />
      {health && (
        <span className="chip">
          {health.data.skus} SKU · {health.data.weeks} 周 ·{' '}
          {Object.values(health.data.rows).reduce((a, b) => a + b, 0).toLocaleString()} 行
        </span>
      )}
    </header>
  )
}

/* ---------------------------------------------------------------- 图标 */
function NavIcon({ name }: { name: string }) {
  const p: Record<string, string> = {
    grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    route: 'M4 6h6a4 4 0 0 1 4 4v8M4 6l3-3M4 6l3 3M14 18l3-3M14 18l3 3',
    layers: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5',
    message: 'M4 5h16v11H8l-4 4V5z',
    target: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z',
    check: 'M4 12.5l5 5L20 6.5',
    spark: 'M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2L12 3z',
  }
  return (
    <svg className="sidenav-ico" width="15" height="15" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d={p[name] ?? p.grid} />
    </svg>
  )
}