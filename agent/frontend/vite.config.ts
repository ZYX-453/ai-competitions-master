import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * 【glass.css 必须排在打包产物之后，否则整层主题被压掉】
 *
 * 现象：页面退回紫色基础皮肤，深紫强调色与玻璃层全部不生效。
 * 根因不是文件没加载（Network 里是 200），而是**层叠顺序**：
 * glass.css 里大量规则与 app.css 是同一个选择器、同一优先级
 * （比如都给 .pg-card 设背景、都声明 :root 的 --accent），
 * 这种情况 CSS 只认后出现的那一条。
 *
 * Vite 会把打包出来的 <link rel="stylesheet"> 自动追加到 </head> 之前，
 * 所以写在 index.html 里的 <link href="/glass.css"> 永远排在它前面 ——
 * 源模板怎么调顺序都没用，必须在构建后重排。
 * 实测：重排前 --accent = #f2f2f3（app.css 的值），重排后 = #236872。
 */
function glassCssLast() {
  return {
    name: 'glass-css-last',
    enforce: 'post',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (ctx.server) return html // dev 下 Vite 自己注入，不干预
        const tag = /<link[^>]+href="\/glass\.css[^"]*"[^>]*>/i
        const m = html.match(tag)
        if (!m) return html
        /* 【必须先剥掉注释再动 </head>】
           踩过的坑：注释里为了说明"插到 </head> 之前"写了这个标签的字面量，
           而 String.replace 只认字符串、不认"这是不是注释里的一段"，
           于是把闭合标签插进了注释中间，整个 head 结构被打断。
           这里先把 <!-- --> 整段去掉算出真正的位置，再去操作原串。 */
        const stripped = html.replace(/<!--[\s\S]*?-->/g, (c) => ' '.repeat(c.length))
        const idx = stripped.lastIndexOf('</head>')
        if (idx < 0) return html
        const withoutTag = html.replace(tag, '')
        // 原位置可能因删 tag 而前移，重新算一次
        const s2 = withoutTag.replace(/<!--[\s\S]*?-->/g, (c) => ' '.repeat(c.length))
        const i2 = s2.lastIndexOf('</head>')
        if (i2 < 0) return withoutTag
        return withoutTag.slice(0, i2) + `  ${m[0]}\n  ` + withoutTag.slice(i2)
      },
    },
  }
}

export default defineConfig({
  plugins: [react(), glassCssLast()],
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 透传到后端，避免 CORS 和硬编码端口
      '/api': { target: 'http://127.0.0.1:8848', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})