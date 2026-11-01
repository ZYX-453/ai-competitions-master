import { Component, type ErrorInfo, type ReactNode } from 'react'

/** 错误边界。
 *
 * 为什么必须有：React 18 里任何一个子组件抛未捕获异常，
 * 默认行为是**卸载整棵组件树**，页面变成一片空白，控制台之外毫无线索。
 * 现场演示时遇到这种白屏是灾难性的——连"哪里错了"都看不出来。
 *
 * 有了边界，至少能显示：出错的组件、错误信息、以及可展开的调用栈，
 * 而且只影响出错的那一块，其它内容照常显示。
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null; info: string }
> {
  state = { error: null as Error | null, info: '' }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ info: info.componentStack ?? '' })
    console.error('[render] 组件渲染失败', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div
        style={{
          border: '1px solid var(--bad-line)',
          background: 'var(--bad-dim)',
          borderRadius: 'var(--r)',
          padding: '11px 13px',
          margin: '0 0 12px',
          fontSize: 12,
          fontFamily: 'var(--mono)',
          color: 'var(--fg-2)',
          lineHeight: 1.6,
        }}
      >
        <div style={{ color: 'var(--bad)', fontWeight: 600, marginBottom: 4 }}>
          渲染出错 · {this.props.label ?? 'unknown'}
        </div>
        <div style={{ color: 'var(--fg)' }}>{String(this.state.error?.message ?? this.state.error)}</div>
        {this.state.info && (
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: 'pointer', color: 'var(--fg-3)', fontSize: 11 }}>调用栈</summary>
            <pre style={{ margin: '6px 0 0', fontSize: 10, color: 'var(--fg-3)', whiteSpace: 'pre-wrap' }}>
              {this.state.info.slice(0, 1200)}
            </pre>
          </details>
        )}
      </div>
    )
  }
}