/* ===========================================================================
 * 统一的数据请求 hook
 *
 * 【为什么值得抽出来】
 * 盘了一遍 6 个页面，它们做的事完全一样：
 *   useEffect(() => { getJson(url).then(setD).catch(setErr) }, [deps])
 * 但每个都缺了同样的两件事，而且缺法各不相同：
 *
 *   ① **竞态**。在归因页快速点 W44 → W45 → W46，三个请求并发。
 *      谁后返回谁生效。如果 W44 的响应最慢（首次算力冷、或有缓存差异），
 *      最终屏幕上是 W44 的数字，而周次标签写着 W46。
 *      这不是"偶尔闪一下"，是**给出了错误的结论** —— 读者会拿
 *      W44 的退货率去解释 W46 的经营动作。而且它不可复现，
 *      演示时几乎撞不到，出问题也查不出来。
 *
 *   ② **无超时**。已在 lib/sse.ts 的 getJson 里补上，但光有超时不够：
 *      超时抛错后如果组件已卸载（用户切走了），setState 会打在
 *      一个不存在的组件上（React 18 不再警告，但仍是泄漏）。
 *
 * 与其在 6 个文件里各贴一遍 alive 标志 + 清理函数，不如收成一个 hook。
 * 这样"新增一个页面"就不需要重新想一遍这些边界。
 *
 * 【为什么用自增序号而不是 AbortController 中止请求】
 * 中止省流量，但这里每个请求都是几 KB 到几十 KB 的 JSON，
 * 而中止会让后端日志里出现一堆 cancelled，排查真实故障时很吵。
 * 序号方案的语义也更贴近需求：**"只认最后一次"**，
 * 而不是"取消前一次"——前者在乱序返回时天然正确，
 * 后者依赖 AbortError 被正确识别，容易在 catch 里漏判。
 * =========================================================================== */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from './sse'

export interface FetchState<T> {
  data: T | null
  err: string
  /** 首次加载中（data 仍为 null）。用于决定显示"加载中"还是"旧数据 + 顶部细条" */
  busy: boolean
  /** 手动重试。递增内部 key，触发重新请求 */
  retry: () => void
  /** 本地改写数据（乐观更新、或从缓存命中时用） */
  setData: (v: T | null) => void
  /**
   * 【data 是哪条 url 换来的】
   *
   * 平时没人关心，但有一类页面必须知道：**自己另存了一份按范围分组的缓存**
   * （见 Dashboard 的 scope 切换）。那种页面会写成
   *   d = data ?? cache[key]
   * 这里的 data 在切换范围后**不会立刻变 null** —— 它还是上一个范围的数，
   * 于是 100ms 内屏幕上是"服饰的标签配全部类目的数字"。
   * 这正是本轮要消灭的那类错误结论。
   *
   * 带回来之后调用方可以写成
   *   const mine = dataUrl === url ? data : null
   * 一句话把"数据是否属于当前这次请求"讲清楚，不用再靠 busy 猜。
   *
   * 【为什么不干脆在 url 变化时把 data 清空】
   * 归因页靠"旧数据继续显示 + 顶部细条"来避免翻周时整页闪成加载中。
   * 清空会让每一次翻周都白屏一次，那是把一个问题换成另一个。
   */
  dataUrl: string | null
}

/**
 * @param url   请求地址。传 null 表示"条件未就绪，先别发"（如 week=0 时）。
 * @param deps  依赖变化时重取。注意 url 已参与比较，deps 只放 url 之外的因素。
 */
export function useFetch<T>(
  url: string | null,
  deps: unknown[] = [],
): FetchState<T> {
  const [data, setData] = useState<T | null>(null)
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [tick, setTick] = useState(0)

  /* 请求序号。每次发起 +1，只有等于当前序号的响应才允许落地。 */
  const seq = useRef(0)
  /* 卸载标志。组件走了就不再 setState —— 光靠序号挡不住
     "请求已返回但组件已卸载"这种情况（序号仍是当前的）。 */
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const retry = useCallback(() => setTick((x) => x + 1), [])

  useEffect(() => {
    if (!url) {
      /* 【url 变 null 时要把旧数据清掉】
         典型场景：用户把筛选从"服饰"切回"全部类目"，
         如果此时 url 暂时为 null（比如等 week 确定），
         留着上一次的服饰数据会让人以为"全部类目就是这个数"。 */
      setData(null)
      setDataUrl(null)
      setBusy(false)
      setErr('')
      return
    }

    const my = ++seq.current
    setBusy(true)
    setErr('')

    getJson<T>(url)
      .then((r) => {
        /* 三重校验：序号是最新的、组件还活着。缺一不可。 */
        if (my !== seq.current || !alive.current) return
        setData(r)
        setDataUrl(url)
        setBusy(false)
      })
      .catch((e: unknown) => {
        if (my !== seq.current || !alive.current) return
        setErr(e instanceof Error ? e.message : String(e))
        setBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, tick, ...deps])

  return { data, err, busy, retry, setData, dataUrl }
}