import { useState } from 'react'

interface LoginProps {
  onLogin: () => void
}

export function Login({ onLogin }: LoginProps) {
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!account.trim()) {
      setError('请输入账号')
      return
    }
    if (!password.trim()) {
      setError('请输入密码')
      return
    }

    setLoading(true)
    // 模拟登录校验，直接放行（实际项目可接后端 /api/login）
    await new Promise((r) => setTimeout(r, 400))
    setLoading(false)
    onLogin()
  }

  return (
    <div className="login-page">
      {/* 科技连线粒子层：极淡，不遮挡卡片 */}
      <svg className="login-particles" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {/* 连线 */}
        <g className="pt-lines">
          <line x1="120" y1="160" x2="320" y2="240" />
          <line x1="320" y1="240" x2="240" y2="420" />
          <line x1="120" y1="160" x2="90"  y2="330" />
          <line x1="90"  y1="330" x2="240" y2="420" />
          <line x1="1180" y1="140" x2="1330" y2="260" />
          <line x1="1330" y1="260" x2="1250" y2="430" />
          <line x1="1180" y1="140" x2="1080" y2="300" />
          <line x1="1080" y1="300" x2="1250" y2="430" />
          <line x1="180" y1="700" x2="360" y2="760" />
          <line x1="360" y1="760" x2="260" y2="840" />
          <line x1="1150" y1="680" x2="1320" y2="740" />
          <line x1="1320" y1="740" x2="1230" y2="830" />
          <line x1="560" y1="90" x2="720" y2="140" />
          <line x1="720" y1="140" x2="880" y2="90" />
        </g>
        {/* 节点 */}
        <g className="pt-dots">
          <circle cx="120" cy="160" r="3" /><circle cx="320" cy="240" r="2.2" />
          <circle cx="90"  cy="330" r="2.6" /><circle cx="240" cy="420" r="2" />
          <circle cx="1180" cy="140" r="3" /><circle cx="1330" cy="260" r="2.2" />
          <circle cx="1250" cy="430" r="2.4" /><circle cx="1080" cy="300" r="1.8" />
          <circle cx="180" cy="700" r="2.6" /><circle cx="360" cy="760" r="2" />
          <circle cx="260" cy="840" r="2.8" /><circle cx="1150" cy="680" r="2.4" />
          <circle cx="1320" cy="740" r="2" /><circle cx="1230" cy="830" r="2.6" />
          <circle cx="560" cy="90" r="2" /><circle cx="720" cy="140" r="2.6" />
          <circle cx="880" cy="90" r="2" />
        </g>
      </svg>

      <div className="login-card">
        <div className="login-brand">
          <h1 className="login-title">智鉴察源</h1>
          <p className="login-subtitle">以智鉴迹，察异动之源</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <input
              id="login-account"
              type="text"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder=" "
              autoComplete="username"
            />
            <label htmlFor="login-account">账号</label>
          </div>

          <div className="login-field">
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder=" "
              autoComplete="current-password"
            />
            <label htmlFor="login-password">密码</label>
          </div>

          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? '登录中…' : '登 录'}
          </button>
        </form>

        <div className="login-foot">
          <a href="#" onClick={(e) => e.preventDefault()}>忘记密码</a>
          <span className="login-sep">·</span>
          <a href="#" onClick={(e) => e.preventDefault()}>隐私协议</a>
        </div>
      </div>
    </div>
  )
}
