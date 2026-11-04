import { useState } from 'react'
import type { StepState, ThoughtEvent } from '../lib/types'

/** 推理链：把 Agent 的每一步摊开。
 *
 * 这个组件是整个产品的说服力所在。评委最常问的一句是
 * "它是不是把答案写死了、只是演一遍给你看？"
 * 唯一能当场反驳的方式，就是让每一步的工具名、参数、耗时、
 * 返回摘要、以及**证据字段**都可展开查看——
 * 而且证据里写的是"扫了哪张表多少行、用什么公式算的"，
 * 任何人都能拿这个去核原始 CSV。
 *
 * 交互取舍：
 * · 证据默认收起。如果一上来全铺开，视线会被淹没，看不到主链。
 * · 步骤本身不做折叠——折叠会让"流式追加"变得难判断（不知道是没到还是被折了）。
 */
export function Chain({ steps, thoughts }: { steps: StepState[]; thoughts: ThoughtEvent[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})

  if (steps.length === 0 && thoughts.length === 0) return null

  return (
    <div className="card chain-card">
      <div className="card-head">
        <h3 className="card-title">推理链</h3>
        <span className="spacer" />
        <span className="step-ms">
          {steps.filter((s) => s.status !== 'running').length}/{steps.length} 步
        </span>
      </div>

      <div className="steps">
        {steps.map((s, i) => (
          <div key={s.id} className={`step ${s.status}`}>
            <div
              className="step-main"
              onClick={() => s.evidence && setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}
              style={{ cursor: s.evidence ? 'pointer' : 'default' }}
            >
              <div className="step-ico">
                {s.status === 'running' ? (
                  <div className="spinner" />
                ) : s.status === 'fail' ? (
                  '!'
                ) : (
                  String(i + 1).padStart(2, '0')
                )}
              </div>

              <div style={{ minWidth: 0 }}>
                <div className="step-title">
                  {s.title}
                  <span className="step-tool">{s.tool}</span>
                  {Object.keys(s.args || {}).length > 0 && (
                    <span className="step-tool">
                      ({Object.entries(s.args).map(([k, v]) => `${k}=${v}`).join(', ')})
                    </span>
                  )}
                </div>
                {s.summary && <div className="step-sum">{s.summary}</div>}
                {s.error && <div className="step-sum" style={{ color: 'var(--bad)' }}>{s.error}</div>}
              </div>

              <div className="step-right">
                {s.ms != null && <span className="step-ms">{s.ms.toFixed(0)}ms</span>}
                {s.evidence && (
                  <span className="step-ms" style={{ color: open[s.id] ? 'var(--accent)' : undefined }}>
                    {open[s.id] ? '−' : '+'}
                  </span>
                )}
              </div>
            </div>

            {open[s.id] && s.evidence && <Evidence evidence={s.evidence} note={s.note} />}
          </div>
        ))}

        {thoughts.map((t, i) => (
          <div key={`th${i}`} className="thought">
            <span className="thought-l">思考</span>
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const EV_LABEL: Record<string, string> = {
  scanned: '扫描',
  formula: '计算式',
  baseline: '基线',
  anomaly_rule: '判定规则',
  why_binom: '为何用二项',
  filter: '筛选条件',
  confound: '混淆检验',
  method_structure: '结构分解',
  method_mechanism: '机制识别',
  lambda1: 'λ1 弹性',
  omega1: 'ω1 弹性',
  why_divide_100: '单位换算',
  why_cost: '口径说明',
  source: '来源',
  note: '说明',
}

function Evidence({ evidence, note }: { evidence: Record<string, unknown>; note?: string }) {
  const entries = Object.entries(evidence).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (entries.length === 0 && !note) return null

  return (
    <div className="evidence">
      {entries.map(([k, v]) => (
        <div className="ev-row" key={k}>
          <span className="ev-k">{EV_LABEL[k] ?? k}</span>
          <span className="ev-v">{renderValue(v)}</span>
        </div>
      ))}
      {note && (
        <div className="ev-row">
          <span className="ev-k">补充</span>
          <span className="ev-v">{note}</span>
        </div>
      )}
    </div>
  )
}

/** 证据值可能是字符串、数字、对象（如 scanned: {panel_weekly: 5890}）。
 * 对象要展开成 "panel_weekly 5,890 行" 这样的人话，而不是 JSON 原文——
 * 现场没人愿意读 JSON。 */
function renderValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return formatNum(v)
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.map(renderValue).join('、')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k} ${typeof val === 'number' ? formatNum(val) : renderValue(val)}`)
      .join(' · ')
  }
  return String(v)
}

function formatNum(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString('zh-CN')
  if (Math.abs(n) < 0.001 && n !== 0) return n.toExponential(2)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}