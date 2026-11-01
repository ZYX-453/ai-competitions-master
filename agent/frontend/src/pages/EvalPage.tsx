/* ===========================================================================
 * 自检页
 *
 * 【为什么要把命令行验收搬上页面】
 * "我自己测过"这句话在评审场合没有说服力——因为无法核实。
 * 做成页面，别人不用听我讲，点一下就知道：有哪些断言、
 * 每一条测的是什么、当前是通过还是失败。
 *
 * 【为什么不现场跑，而是展示固化清单】
 * 跑一遍 check_review_analytics 要 20 多秒，还会重复加载 17,660 条语料。
 * 页面每次刷新都跑一遍没意义，而且会让"打开自检页"变成一件慢事。
 * 所以这里给的是**清单 + 当前数据规模**，让人能对照
 * "这些断言是在多大的数据上跑的"。
 *
 * 【状态字段必须能表示"没测"，不能只有 pass/fail】
 * run_all.py 需要 LLM Key，本机没配时它不是失败，是"没跑"。
 * 把它显示成红叉会让人以为代码坏了；
 * 显示成绿勾则是撒谎。所以单独有一个 needs_key 状态。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { EvalData } from '../lib/pages'
import { Card, Stat, Loading, Fail, nf } from '../components/PageKit'

const STATUS: Record<string, { txt: string; cls: string }> = {
  pass: { txt: '通过', cls: 'ok' },
  fail: { txt: '失败', cls: 'bad' },
  needs_key: { txt: '未跑（需 Key）', cls: 'warn' },
}

export function EvalPage() {
  const [d, setD] = useState<EvalData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    setErr('')
    getJson<EvalData>('/api/pages/eval').then(setD).catch((e) => setErr(String(e)))
  }, [tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="读验收清单" />

  const totalAssert = d.suites.reduce((s, x) => s + x.assertions, 0)
  const passed = d.suites.filter((x) => x.status === 'pass').reduce((s, x) => s + x.assertions, 0)
  const warns = d.suites.reduce((s, x) => s + x.warn, 0)

  const scale = d.data_scale
  const scaleItems: { k: string; label: string }[] = [
    { k: 'panel_rows', label: '周度面板行' },
    { k: 'return_rows', label: '退货记录' },
    { k: 'review_rows', label: '评论监控' },
    { k: 'train_rows', label: '训练集' },
    { k: 'log_rows', label: '客服工单' },
    { k: 'sku_rows', label: 'SKU 主表' },
    { k: 'corpus_rows', label: '评论语料池' },
    { k: 'corpus_entities', label: '语料实体数' },
  ]

  return (
    <div className="pg">
      <div className="pg-kpi-row">
        <Stat label="断言总数" value={totalAssert} unit="条"
          sub={`${passed} 条在通过状态`} />
        <Stat label="验收套件" value={d.suites.length} unit="套"
          sub={d.suites.map((s) => s.title).join(' · ')} />
        <Stat label="警告" value={warns} unit="项"
          tone={warns > 0 ? 'warn' : 'good'}
          sub={warns > 0 ? '不阻断，但要知道' : '无警告'} />
        <Stat label="语料池" value={nf(scale.corpus_rows ?? 0)} unit="条"
          sub={`${scale.corpus_entities ?? 0} 个实体 · ${scale.corpus_categories ?? 0} 个类目`} />
      </div>

      {d.suites.map((s) => {
        const st = STATUS[s.status] ?? { txt: s.status, cls: '' }
        return (
          <Card
            key={s.name}
            title={
              <span className="pg-suite-title">
                <span className={`pg-dot-status ${st.cls}`} />
                {s.title}
                <code className="pg-suite-file">{s.name}</code>
                <span className={`pg-tag ${st.cls === 'ok' ? 't-good' : st.cls === 'bad' ? 't-bad' : 't-warn'}`}>
                  {st.txt}
                </span>
                <span className="pg-suite-n">{s.assertions} 条断言{s.warn > 0 && ` · ${s.warn} 警告`}</span>
              </span>
            }
            pad={false}
          >
            <table className="pg-table">
              <thead>
                <tr>
                  <th className="narrow">#</th>
                  <th>这一节测什么</th>
                  <th>具体判据</th>
                </tr>
              </thead>
              <tbody>
                {s.sections.map((sec) => (
                  <tr key={sec.n}>
                    <td className="num dim">{sec.n}</td>
                    <td className="strong">{sec.title}</td>
                    <td className="pg-wrap">{sec.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )
      })}

      <div className="pg-2col">
        <Card title="数据规模快照" note={<>上面那些断言，就是在这么多数据上跑的。</>} pad={false}>
          <div className="pg-scale">
            {scaleItems.filter((x) => scale[x.k] != null).map((x) => (
              <div className="pg-scale-row" key={x.k}>
                <span className="pg-scale-k">{x.label}</span>
                <span className="pg-scale-v num">{nf(scale[x.k])}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card
          title={d.independence.claim}
          note={<>这不是靠约定，是靠文件里根本没有那些列。</>}
        >
          <div className="pg-indep">
            <div className="pg-indep-how">{d.independence.how}</div>
            <ul className="pg-indep-proof">
              {d.independence.proof.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </div>
        </Card>
      </div>

      <div className="pg-foot">
        判定逻辑都在 check_*.py 里，可以随时在命令行复跑。
        这一页展示的是清单与当前数据规模，不现场执行。
      </div>
    </div>
  )
}