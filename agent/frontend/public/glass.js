/* ===========================================================================
 * 玻璃交互增强层 · 六件套
 * ① 液态高亮块  ② 光跟着鼠标走  ③ 模块就地放大
 * ④ 水滴分裂筛选  ⑤ Ctrl+K 命令面板  ⑥ 玻璃停靠栏
 * 纯原生 JS，挂在 React 之外（document.body），靠事件委托与
 * MutationObserver 跟随重渲染，不侵入原代码。
 * =========================================================================== */
(() => {
  'use strict';

  const ICONS = {
    grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    route: 'M4 6h6a4 4 0 0 1 4 4v8M4 6l3-3M4 6l3 3M14 18l3-3M14 18l3 3',
    layers: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5',
    message: 'M4 5h16v11H8l-4 4V5z',
    target: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z',
    check: 'M4 12.5l5 5L20 6.5',
    alert: 'M12 4l9 16H3L12 4zM12 10v4M12 17.2v.4',
    spark: 'M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2L12 3z',
    search: 'M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM15.5 15.5L21 21',
  };
  const FALLBACK_PAGES = [
    { key: 'dashboard', title: '态势全景', subtitle: '现在怎么样、哪里最疼', icon: 'grid' },
    { key: 'attribution', title: '异动归因', subtitle: '这一周为什么涨了', icon: 'route' },
    { key: 'reasons', title: '成因治理', subtitle: '哪个原因值得管', icon: 'layers' },
    { key: 'plan', title: '预警方略', subtitle: '要处理什么', icon: 'alert' },
    { key: 'reviews', title: '众声洞察', subtitle: '用户在说什么', icon: 'message' },
    { key: 'competitors', title: '竞品棱镜', subtitle: '我们处在什么位置', icon: 'target' },
    { key: 'chat', title: '问一问', subtitle: '有问题，直接问', icon: 'spark' },
  ];
  /* 导航名 / 建议问法的雅化映射：拦截 /api/pages/nav 与 /api/meta 的响应，
     让 React 页头与玻璃层（停靠栏、命令面板）用同一套更高级的说法 */
  const NAV_TITLES = {
    '总览': '态势全景', '归因': '异动归因', '原因治理': '成因治理',
    '预警策划': '预警方略', '评论洞察': '众声洞察', '竞品雷达': '竞品棱镜', '问答': '问一问',
  };
  const LABEL_MAP = {
    '双11 那周为什么退货率高': '双十一当周，退货率因何攀升',
    '最近一周有问题吗': '最近一周，是否存有异动',
    '退货到底亏了多少钱': '退货造成的损失，究竟几何',
    '补多少钱最划算': '补偿档位定在多少最为合算',
    '服饰类目怎么回事': '服饰类目的异动从何而来',
    '给我一套可执行方案': '给出一套可落地的治理方案',
    '评论情感怎么样': '评论情绪的整体走向如何',
    '差评都在说什么': '差评集中指向哪些问题',
    '用户画像是什么样': '顾客群像呈现出何种轮廓',
    '和竞品比怎么样': '与竞品相比处于何种位置',
    '数据是怎么来的': '数据的来源与构成',
  };
  const DROP_OPTIONS = ['今天', '本周', '本月', '全年'];

  const svg = (name) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${ICONS[name] || ICONS.grid}"/></svg>`;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let NAV = null;
  let expandedCard = null;
  let stripEl = null;

  const isChatPage = () => !!document.querySelector('.app.app-chat');
  const curPage = () => (location.hash.replace(/^#\/?/, '').split('/')[0] || 'dashboard');
  const topCards = () =>
    [...document.querySelectorAll('.app-scroll .pg-card')]
      .filter((c) => c.offsetParent !== null && !c.parentElement.closest('.pg-card'));

  /* ================================================================ ⑥ 停靠栏 */
  const dock = document.createElement('div');
  dock.id = 'glass-dock';
  const pill = document.createElement('div');
  pill.id = 'glass-pill';
  dock.appendChild(pill);

  function buildDock() {
    const pages = (NAV && NAV.pages) || FALLBACK_PAGES;
    dock.querySelectorAll('.glass-dock-item,.glass-dock-sep').forEach((n) => n.remove());
    pages.forEach((p) => {
      const b = document.createElement('button');
      b.className = 'glass-dock-item';
      b.dataset.key = p.key;
      b.innerHTML = `${svg(p.icon)}<span class="glass-dock-tip">${esc(p.title)}</span>`;
      b.addEventListener('click', () => { location.hash = `#/${p.key}`; });
      dock.appendChild(b);
    });
    const sep = document.createElement('div');
    sep.className = 'glass-dock-sep';
    dock.appendChild(sep);
    const k = document.createElement('button');
    k.className = 'glass-dock-item';
    k.dataset.key = '__cmdk';
    k.title = '命令面板 (Ctrl+K)';
    k.innerHTML = `${svg('search')}<span class="glass-dock-tip">命令面板 · Ctrl+K</span>`;
    k.addEventListener('click', openCmdk);
    dock.appendChild(k);
    syncDock(true);
  }

  function syncDock(instant) {
    const key = curPage();
    const items = [...dock.querySelectorAll('.glass-dock-item')];
    const active = items.find((i) => i.dataset.key === key);
    items.forEach((i) => i.classList.toggle('on', i === active));
    if (active) movePill(active, instant);
  }

  /* ================================================================ ① 液态高亮 */
  let pillRect = null;
  function movePill(item, instant) {
    const dr = dock.getBoundingClientRect();
    const r = item.getBoundingClientRect();
    const x = r.left - dr.left, y = r.top - dr.top + 3;
    const w = r.width, h = r.height - 6;
    pill.classList.toggle('settle', !instant);

    if (instant || !pillRect) {
      pill.style.transition = 'none';
      pill.style.width = w + 'px';
      pill.style.height = h + 'px';
      pill.style.transform = `translate(${x}px, ${y}px)`;
      pillRect = { x, y, w, h };
      return;
    }
    /* 液态：先朝目标方向拉长（沿移动方向 scaleX），再带弹性回弹落到新位置 */
    const midX = (pillRect.x + x) / 2;
    const stretch = Math.max(1, Math.abs(x - pillRect.x) / Math.max(w, 1) * 0.5 + 1);
    pill.style.transition = 'transform .16s cubic-bezier(.4,0,.6,1), width .16s';
    pill.style.transform = `translate(${midX}px, ${y}px) scaleX(${stretch.toFixed(2)})`;
    setTimeout(() => {
      pill.style.transition = 'transform .5s cubic-bezier(.3,1.7,.35,1), width .5s cubic-bezier(.3,1.7,.35,1)';
      pill.style.transform = `translate(${x}px, ${y}px) scaleX(1)`;
      pill.style.width = w + 'px';
      pill.style.height = h + 'px';
    }, 170);
    pillRect = { x, y, w, h };
  }

  /* ================================================================ ② 光跟着鼠标走 */
  let tiltCard = null, rafPending = false, lastMove = null;
  document.addEventListener('mousemove', (e) => {
    const card = e.target.closest ? e.target.closest('.app .pg-card') : null;
    if (lastMove && card === lastMove.card) {
      lastMove.e = e; if (!rafPending) { rafPending = true; requestAnimationFrame(applySpot); }
      return;
    }
    if (lastMove && lastMove.card !== card) resetSpot(lastMove.card);
    lastMove = { card, e }; if (!rafPending) { rafPending = true; requestAnimationFrame(applySpot); }
  }, { passive: true });

  function applySpot() {
    rafPending = false;
    if (!lastMove) return;
    const { card, e } = lastMove;
    if (!card || !document.contains(card)) return;
    const r = card.getBoundingClientRect();
    card.classList.add('gl-on');
    card.style.setProperty('--gx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
    card.style.setProperty('--gy', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
    /* 轻微 3D 倾斜：像一盏灯压在卡片上（放大的卡片不倾斜） */
    if (!card.classList.contains('glass-expanded') && r.width > 160 && r.height > 90) {
      const rx = -((e.clientY - r.top) / r.height - .5) * 3.2;
      const ry = ((e.clientX - r.left) / r.width - .5) * 3.2;
      card.classList.add('gl-tilt');
      card.style.transform = `perspective(1100px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`;
      tiltCard = card;
    }
  }
  function resetSpot(card) {
    if (!card) return;
    card.classList.remove('gl-on', 'gl-tilt');
    if (card === tiltCard) { card.style.transform = ''; tiltCard = null; }
  }
  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest && e.target.closest('.app .pg-card');
    if (card && (!e.relatedTarget || !e.relatedTarget.closest('.app .pg-card') || e.relatedTarget.closest('.app .pg-card') !== card)) {
      if (lastMove && lastMove.card === card) lastMove = null;
      resetSpot(card);
    }
  });

  /* ================================================================ ③ 模块就地放大 */
  function cardTitle(card) {
    const t = card.querySelector('.pg-card-t');
    return t ? t.textContent.trim() : '图表';
  }

  function ensureExpButtons() {
    if (isChatPage()) return;
    topCards().forEach((card) => {
      if (card.querySelector(':scope > .glass-exp')) return;
      const b = document.createElement('button');
      b.className = 'glass-exp';
      b.title = '就地放大';
      b.textContent = '⤢';
      card.appendChild(b);
    });
  }

  /* 捕获阶段拦截 ⤢ / ✕：抢在 React 的根节点委托之前吃掉事件，
     否则总览页自带跳转的卡片（异常周→归因页）会抢走这次点击 */
  document.addEventListener('click', (e) => {
    const expBtn = e.target.closest('.glass-exp');
    if (expBtn) {
      e.preventDefault(); e.stopImmediatePropagation(); e.stopPropagation();
      const card = expBtn.parentElement.closest('.pg-card');
      if (card) expandCard(card);
      return;
    }
    if (e.target.closest('.glass-close')) {
      e.preventDefault(); e.stopImmediatePropagation(); e.stopPropagation();
      collapseAll();
      return;
    }
    /* 缩略图切换：捕获阶段吃掉事件，直接在这里换展开的卡片 */
    const thumb = e.target.closest('.glass-thumb');
    if (thumb && thumb.__card) {
      e.preventDefault(); e.stopImmediatePropagation(); e.stopPropagation();
      const c = thumb.__card;
      if (expandedCard !== c) {
        expandCard(c);
        if (stripEl) stripEl.querySelectorAll('.glass-thumb').forEach((t) => t.classList.toggle('on', t === thumb));
      }
    }
  }, true);

  function expandCard(card) {
    if (expandedCard === card) return;
    if (expandedCard) collapseAll(true);
    resetSpot(card);
    const others = topCards().filter((c) => c !== card);
    const first = card.getBoundingClientRect();

    card.classList.add('glass-expanded');
    others.forEach((c) => c.classList.add('glass-min'));

    /* FLIP：从原位放大到全屏 */
    const last = card.getBoundingClientRect();
    const dx = first.left - last.left, dy = first.top - last.top;
    const sx = first.width / last.width, sy = first.height / last.height;
    card.style.transition = 'none';
    card.style.transformOrigin = 'top left';
    card.style.transform = `translate(${dx}px, ${dy}px) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
    requestAnimationFrame(() => {
      card.style.transition = 'transform .5s cubic-bezier(.22,1,.36,1)';
      card.style.transform = '';
      setTimeout(() => { card.style.transition = ''; card.style.transformOrigin = ''; }, 520);
    });

    expandedCard = card;
    buildStrip(card, others);
  }

  function buildStrip(active, others) {
    removeStrip();
    stripEl = document.createElement('div');
    stripEl.id = 'glass-strip';
    others.forEach((c) => {
      const w = c.offsetWidth || 400;
      const scale = 170 / w;
      const clone = c.cloneNode(true);
      clone.querySelector(':scope > .glass-exp')?.remove();
      clone.querySelectorAll('svg[width="0"]').forEach((s) => s.remove());
      clone.style.width = w + 'px';
      clone.classList.remove('glass-min');

      const thumb = document.createElement('div');
      thumb.className = 'glass-thumb';
      thumb.appendChild(Object.assign(document.createElement('div'), { className: 'glass-thumb-in' }));
      thumb.firstChild.appendChild(clone);
      thumb.appendChild(Object.assign(document.createElement('div'), { className: 'glass-thumb-cap', textContent: cardTitle(c) }));
      const inn = thumb.firstChild;
      inn.style.width = w + 'px';
      inn.style.transform = `scale(${scale.toFixed(4)})`;
      thumb.__card = c;
      stripEl.appendChild(thumb);
    });
    document.body.appendChild(stripEl);

    const close = document.createElement('button');
    close.className = 'glass-close';
    close.title = '收回';
    close.textContent = '✕';
    close.addEventListener('click', collapseAll);
    expandedCard.appendChild(close);
  }

  function removeStrip() {
    if (stripEl) { stripEl.remove(); stripEl = null; }
    document.querySelectorAll('.glass-close').forEach((n) => n.remove());
  }

  function collapseAll(silent) {
    if (expandedCard) {
      expandedCard.classList.remove('glass-expanded');
      expandedCard.style.transform = '';
      expandedCard = null;
    }
    document.querySelectorAll('.pg-card.glass-min').forEach((c) => c.classList.remove('glass-min'));
    removeStrip();
    if (!silent) ensureExpButtons();
  }

  /* ================================================================ ④ 水滴分裂筛选 */
  let dropChoice = '本月';
  function injectDrop() {
    const host = document.querySelector('.pagehead-r');
    if (!host || host.querySelector('#glass-drop') || isChatPage()) return;
    const b = document.createElement('button');
    b.id = 'glass-drop';
    b.innerHTML = `<span class="drop-dot"></span><span class="drop-label">${esc(dropChoice)}</span>`;
    b.addEventListener('click', (e) => { e.stopPropagation(); splitDrop(b); });
    host.insertBefore(b, host.firstChild);
  }

  function splitDrop(pillBtn) {
    if (document.querySelector('#glass-drop-menu')) { mergeDrop(null); return; }
    const r = pillBtn.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'glass-drop-menu';
    DROP_OPTIONS.forEach((opt, i) => {
      const d = document.createElement('div');
      d.className = 'glass-droplet' + (opt === dropChoice ? ' pick' : '');
      d.textContent = opt;
      d.addEventListener('click', (ev) => { ev.stopPropagation(); mergeDrop(opt); });
      menu.appendChild(d);
      setTimeout(() => d.classList.add('in'), 40 + i * 70);
    });
    document.body.appendChild(menu);
    const mw = DROP_OPTIONS.length * 92;
    menu.style.left = Math.max(10, r.left + r.width / 2 - mw / 2) + 'px';
    menu.style.top = (r.top - 46) + 'px';
    pillBtn.style.transform = 'scale(1.06)';
    setTimeout(() => (pillBtn.style.transform = ''), 260);
    /* 点击别处收拢 */
    setTimeout(() => document.addEventListener('click', mergeDropOutside, { once: true }), 0);
  }
  function mergeDropOutside() { mergeDrop(null); }

  function mergeDrop(choice) {
    const menu = document.querySelector('#glass-drop-menu');
    document.removeEventListener('click', mergeDropOutside);
    if (!menu) return;
    const drops = [...menu.querySelectorAll('.glass-droplet')];
    if (choice) {
      drops.forEach((d) => d.classList.toggle('pick', d.textContent === choice));
      setTimeout(() => {
        drops.forEach((d) => { if (d.textContent !== choice) d.classList.remove('in'); });
        setTimeout(() => {
          menu.remove();
          applyDropChoice(choice);
        }, 320);
      }, 160);
    } else {
      drops.forEach((d) => d.classList.remove('in'));
      setTimeout(() => menu.remove(), 360);
    }
  }

  function applyDropChoice(choice) {
    dropChoice = choice;
    const label = document.querySelector('#glass-drop .drop-label');
    if (label) {
      label.style.transition = 'opacity .18s, transform .18s';
      label.style.opacity = '0';
      label.style.transform = 'translateY(-8px)';
      setTimeout(() => {
        label.textContent = choice;
        label.style.opacity = '1';
        label.style.transform = 'translateY(0)';
      }, 190);
    }
    /* 选中后指标数字滚到新值（数据口径不变，滚当前值） */
    document.querySelectorAll('.app-scroll .pg-stat-v, .app-scroll .kpi-v, .app-scroll .stat-v').forEach(rollNumber);
  }

  function rollNumber(el) {
    if (!el || !el.textContent.trim()) return;
    const old = el.innerHTML;
    el.style.position = 'relative';
    el.style.display = 'inline-block';
    el.style.overflow = 'hidden';
    el.innerHTML = `<span class="glass-roll-old">${old}</span><span class="glass-roll-new" style="display:inline-block">${old}</span>`;
    setTimeout(() => {
      el.textContent = el.textContent; /* 复原为纯文本 */
      el.style.position = ''; el.style.display = ''; el.style.overflow = '';
    }, 560);
  }

  /* ================================================================ ⑤ Ctrl+K 命令面板 */
  const cmdk = document.createElement('div');
  cmdk.id = 'glass-cmdk';
  cmdk.innerHTML = `
    <div class="glass-cmdk-panel">
      <input class="glass-cmdk-input" placeholder="跳到哪一页… 敲两三个字就行" />
      <div class="glass-cmdk-list"></div>
      <div class="glass-cmdk-foot"><span><kbd>↑↓</kbd> 选择</span><span><kbd>Enter</kbd> 跳转</span><span><kbd>Esc</kbd> 关闭</span></div>
    </div>`;
  const cmdkInput = cmdk.querySelector('.glass-cmdk-input');
  const cmdkList = cmdk.querySelector('.glass-cmdk-list');
  let cmdkSel = 0, cmdkItems = [];

  function openCmdk() {
    cmdk.classList.add('open');
    cmdkInput.value = '';
    renderCmdk('');
    setTimeout(() => cmdkInput.focus(), 30);
  }
  function closeCmdk() { cmdk.classList.remove('open'); }

  function renderCmdk(q) {
    const pages = (NAV && NAV.pages) || FALLBACK_PAGES;
    const query = q.trim().toLowerCase();
    cmdkItems = pages.filter((p) =>
      !query || p.title.toLowerCase().includes(query) || p.subtitle.toLowerCase().includes(query) || p.key.includes(query));
    cmdkSel = 0;
    if (!cmdkItems.length) {
      cmdkList.innerHTML = `<div class="glass-cmdk-empty">没有匹配的页面</div>`;
      return;
    }
    const rx = new RegExp('(' + query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
    cmdkList.innerHTML = cmdkItems.map((p, i) => `
      <div class="glass-cmdk-item${i === 0 ? ' sel' : ''}" data-key="${p.key}">
        <span class="ci-ico">${svg(p.icon)}</span>
        <span style="min-width:0">
          <div class="ci-t">${query ? esc(p.title).replace(rx, '<em class="ci-hl">$1</em>') : esc(p.title)}</div>
          <div class="ci-s">${esc(p.subtitle)}</div>
        </span>
      </div>`).join('');
  }

  cmdkInput.addEventListener('input', () => renderCmdk(cmdkInput.value));
  cmdkInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!cmdkItems.length) return;
      cmdkSel = (cmdkSel + (e.key === 'ArrowDown' ? 1 : -1) + cmdkItems.length) % cmdkItems.length;
      cmdkList.querySelectorAll('.glass-cmdk-item').forEach((n, i) => n.classList.toggle('sel', i === cmdkSel));
      cmdkList.querySelector('.sel')?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      const p = cmdkItems[cmdkSel];
      if (p) { location.hash = `#/${p.key}`; closeCmdk(); }
    }
  });
  cmdkList.addEventListener('click', (e) => {
    const item = e.target.closest('.glass-cmdk-item');
    if (item) { location.hash = `#/${item.dataset.key}`; closeCmdk(); }
  });
  cmdk.addEventListener('click', (e) => { if (e.target === cmdk) closeCmdk(); });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); openCmdk(); }
    else if (e.key === 'Escape') { closeCmdk(); if (expandedCard) collapseAll(); }
  });

  /* ================================================================ fetch 重映射 */
  (function wrapFetch() {
    if (window.__glassFetchWrapped) return;
    window.__glassFetchWrapped = true;
    const _f = window.fetch.bind(window);
    window.fetch = (input, init) => _f(input, init).then((res) => {
      let url = '';
      try { url = typeof input === 'string' ? input : (input && input.url) || ''; } catch (_) {}
      if (url.indexOf('/api/pages/nav') < 0 && url.indexOf('/api/meta') < 0) return res;
      return res.clone().json().then((d) => {
        if (Array.isArray(d && d.pages)) d.pages.forEach((p) => {
          if (NAV_TITLES[p.title]) p.title = NAV_TITLES[p.title];
          if (p.key === 'chat') p.subtitle = '有问题，直接问';
        });
        if (Array.isArray(d && d.suggestions)) d.suggestions.forEach((s) => { if (LABEL_MAP[s.label]) s.label = LABEL_MAP[s.label]; });
        return new Response(JSON.stringify(d), {
          status: res.status, statusText: res.statusText,
          headers: { 'Content-Type': 'application/json;charset=utf-8' },
        });
      }).catch(() => res);
    });
  })();

  /* ================================================================ 用户画像 · 小红书海报版式 */
  /* 整卡替换成海报布局：顶部「插画 + 标题卡 | 边界提示卡」双栏，
     下方 2×2 四组条形图卡（玫瑰/薄荷/雾紫/杏黄四色渐变条）。
     数据从原 DOM 读：组名（.pg-ps-h 首个文本节点）、
     条目（.pg-ps-signal 标签 + .pg-ps-n 的 n=数字）。 */
  /* 画像头像：内联 SVG 中性剪影（无性别/年龄特征，紫灰配色，不依赖外部图片） */
  const PERSONA_IMG = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 112">' +
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="#CDC2E8"/><stop offset="1" stop-color="#A99BDD"/>' +
    '</linearGradient></defs>' +
    '<rect width="112" height="112" fill="url(#g)"/>' +
    '<circle cx="56" cy="44" r="20" fill="#FFFFFF" opacity=".85"/>' +
    '<path d="M20 98c4-21 18-31 36-31s32 10 36 31z" fill="#FFFFFF" opacity=".85"/>' +
    '</svg>');

  function enhancePersona() {
    const root = document.querySelector('.pg-persona');
    if (!root || root.dataset.glassPs2 === '1') return;
    const groups = [...root.querySelectorAll('.pg-ps-group')].map((grp) => {
      const h = grp.querySelector('.pg-ps-h');
      const name = h ? h.childNodes[0].textContent.trim() : '';
      const items = [...grp.querySelectorAll('.pg-ps-item')].map((it) => {
        const lab = it.querySelector('.pg-ps-signal');
        const el = it.querySelector('.pg-ps-n');
        const m = el && el.textContent.match(/([\d,]+)/);
        return { label: lab ? lab.textContent.trim() : '', n: m ? parseInt(m[1].replace(/,/g, ''), 10) : 0 };
      });
      return { name, items };
    }).filter((g) => g.name && g.items.length);
    if (!groups.length) return;
    const card = root.closest('.pg-card');
    const FILLS = ['f1', 'f2', 'f3', 'f4'];
    const box = document.createElement('div');
    box.className = 'glass-ps2';
    box.innerHTML =
      `<div class="ps2-top">` +
      `<div class="ps2-intro"><img src="${PERSONA_IMG}" alt="典型用户画像" />` +
      `<div class="ps2-intro-t">评论侧写 · 典型画像<span>由评论文本归纳，非人口属性</span></div></div>` +
      `<div class="ps2-warn"><b>这组画像回答不了什么</b><ul>` +
      `<li><em>年龄、性别、收入、城市</em>——评论里没有这些信息，任何「画像」里出现这几项都是编的</li>` +
      `<li><em>未评论用户的行为</em>——这里是评论样本，不是全部购买用户，沉默的大多数没有出现在这里</li>` +
      `<li><em>人群占比的代表性</em>——评论者的构成和购买者不一定一致（爱写评论的人本身就偏极端）</li>` +
      `</ul></div></div>` +
      `<div class="ps2-grid">` +
      groups.map((g, gi) => {
        const maxN = Math.max(...g.items.map((i) => i.n), 1);
        return `<div class="ps2-card"><div class="ps2-card-t">${g.name}</div>` +
          g.items.map((i) =>
            `<div class="ps2-row"><span class="lab">${i.label}</span>` +
            `<span class="track"><i class="${FILLS[gi % 4]}" style="width:${i.n > 0 ? Math.max(i.n / maxN * 100, 3).toFixed(1) : 0}%"></i></span>` +
            `<b class="val">n=${i.n.toLocaleString()}</b></div>`).join('') +
          `</div>`;
      }).join('') +
      `</div>`;
    if (card) {
      const head = card.querySelector('.pg-card-h');
      if (head) head.style.display = 'none';
      [...card.children].forEach((ch) => ch.remove());
      card.appendChild(box);
    } else {
      root.replaceWith(box);
    }
    root.dataset.glassPs2 = '1';
  }

  /* ================================================================ 分周情感走势 · 粉白模块标记 */
  /* 只给众声洞察页的走势卡打 .glass-trend 类，CSS 据此把这一张卡
     染成浅粉白模块（加粗线条、放大坐标轴），其他图表不受影响。 */
  function tagTrendCard() {
    document.querySelectorAll('.pg-card:not(.glass-trend) .pg-card-t').forEach((t) => {
      if (t.textContent.includes('分周情感走势')) {
        t.closest('.pg-card').classList.add('glass-trend');
      }
    });
  }

  /* ================================================================ 登录门
     粉白报告风的居中登录卡。会话内登录一次即可（sessionStorage），
     通过后整层淡出露出应用。

     【这一层不校验口令，所以必须把这件事写在脸上】
     它原本长着一个真的登录页的样子：账号 / 密码 / 忘记密码 / 隐私协议，
     但 done() 只判断两个框非空，随便填什么都能进。这个落差会直接坑到人：
       · 有人把真口令输进去（想过一道正经门），其实没必要冒这个险；
       · 更多人点了「忘记密码」，弹出一句「请联系管理员重置密码」——
         而根本不存在管理员，也没有密码可重置，这句话是纯死胡同；
       · 少数人会以为"我是不是没权限"，就此放弃。
     所以删掉「忘记密码」这个假出口，改成一行明确的提示语。
     一个演示门面可以简，但不能假装自己有它没有的能力。 */
  function initLoginGate() {
    if (sessionStorage.getItem('cj-login') === '1') return;
    const ov = document.createElement('div');
    ov.className = 'glass-login';
    ov.innerHTML =
      `<div class="gl-card">` +
      `<div class="gl-brand">澄鉴</div>` +
      `<div class="gl-sub">以循证之链，释一周异动之因</div>` +
      `<div class="gl-field"><input type="text" placeholder="账号（任意）" autocomplete="username"></div>` +
      `<div class="gl-field"><input type="password" placeholder="密码（任意）" autocomplete="current-password"></div>` +
      `<div class="gl-err">两个框都要填，填什么都可以</div>` +
      `<button class="gl-btn">登 录</button>` +
      `<div class="gl-hint">演示环境 · 不校验口令，任意账号密码直接进入</div>` +
      `</div>`;
    document.body.appendChild(ov);
    const userInput = ov.querySelectorAll('input')[0];
    const passInput = ov.querySelectorAll('input')[1];
    const err = ov.querySelector('.gl-err');
    const fail = (msg) => { err.textContent = msg; err.classList.add('on'); };
    const done = () => {
      if (!userInput.value.trim() || !passInput.value.trim()) { fail('两个框都要填，填什么都可以'); return; }
      sessionStorage.setItem('cj-login', '1');
      ov.classList.add('bye');
      setTimeout(() => ov.remove(), 480);
    };
    ov.querySelector('.gl-btn').addEventListener('click', done);
    ov.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(); });
    [userInput, passInput].forEach((i) =>
      i.addEventListener('input', () => err.classList.remove('on')));
    setTimeout(() => userInput.focus(), 60);
  }

  /* ================================================================ 蝴蝶图 → 左右双栏对比条形
     左栏「我们的数据」= 本店常说（深青绿条），右栏「竞品数据」= 对标常说
     （淡灰紫条、透明度更低、视觉更轻）。

     【为什么两栏各自独立成列，不按词合并成一张表】
     两侧的词集在数据层面就是互斥的：后端给的是 only_in_target
     （只在本店高频）和 only_in_base（只在竞品高频），按定义没有交集。
     早先那版把两侧读进一个 Map、按词合并，于是每一行必然只有一侧有条、
     另一侧是「—」，还按"两栏宽度的最大值"混排 —— 左栏第 1 行、
     右栏第 2 行这样交错，读者会误以为在对比同一个词。
     正确形态是两列各自排序、各自归一：左列回答"我们爱说什么"，
     右列回答"他们爱说什么"，中间的分隔线只表示这是两组不同的词。 */
  function enhanceButterfly() {
    document.querySelectorAll('.pg-bfly-wrap').forEach((wrap) => {
      if (wrap.dataset.glassBfly === '1') return;
      const sides = { ours: [], theirs: [] };
      wrap.querySelectorAll('.pg-bfly-side').forEach((side) => {
        const key = side.classList.contains('ours') ? 'ours' : 'theirs';
        side.querySelectorAll('.pg-bfly-row').forEach((r) => {
          const w = r.querySelector('.pg-bfly-w');
          const n = r.querySelector('.pg-bfly-n');
          const i = r.querySelector('.pg-bfly-bar i');
          if (!w || !i) return;
          sides[key].push({
            word: w.textContent.trim(),
            n: n ? n.textContent.trim() : '',
            p: parseFloat(i.style.width) || 0,
          });
        });
      });
      if (!sides.ours.length && !sides.theirs.length) return;
      const col = (list, cls) =>
        `<div class="gb3-col ${cls}">` +
        list.map((r) =>
          `<div class="gb3-row" title="${r.word} · ${r.n || '—'} 条">` +
          `<span class="w">${r.word}</span>` +
          `<span class="cell"><i class="track"><i class="bar" style="width:${r.p.toFixed(1)}%"></i></i>` +
          `<b class="n">${r.n || '—'}</b></span>` +
          `</div>`).join('') +
        `</div>`;
      const box = document.createElement('div');
      box.className = 'glass-bfly3';
      box.innerHTML =
        `<div class="gb3-head"><span class="ho">我们的数据 · 本店常说</span>` +
        `<span class="ht">竞品数据 · 对标常说</span></div>` +
        `<div class="gb3-body">` +
        col(sides.ours, 'co') + col(sides.theirs, 'ct') +
        `</div>` +
        `<div class="gb3-note">条长 = 该侧内部的差异幅度（按 log₂ 比值各自归一），所以左右两栏的长度不可直接相减；` +
        `两栏的词集按定义不重叠 ——「只有本店说」和「只有竞品说」本来就不是同一批词。</div>`;
      wrap.style.display = 'none';
      wrap.after(box);
      wrap.dataset.glassBfly = '1';
    });
  }

  /* ================================================================ 态势全景 · 问候横幅 */
  /* 参考医疗 B 端看板的「HELLO, SOPHIA!」：时间问候 + 日期，给数字看板一点人的温度 */
  function enhanceDashGreeting() {
    if (!document.querySelector('.pg-kpi-row')) return;
    const kpiRow = document.querySelector('.pg-kpi-row');
    if (kpiRow.previousElementSibling && kpiRow.previousElementSibling.classList.contains('glass-greet')) return;
    const h = new Date().getHours();
    const word = h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
    const date = new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
    const greet = document.createElement('div');
    greet.className = 'glass-greet';
    greet.innerHTML =
      `<span class="gg-word">${word}</span>` +
      `<span class="gg-sub">这是店铺的近况速览</span>` +
      `<span class="gg-date">${date}</span>`;
    kpiRow.parentElement.insertBefore(greet, kpiRow);
  }

  /* ================================================================ 自定义工作台 */
  /* 参考可编辑小组件看板：网格布局，每个组件可循环调大小（S/M/L）、
     可切换图表类型（折线/柱状/环形/数字），布局存 localStorage。 */
  const BOARD_LS = 'glass-board-v1';
  const fmtWan = (v) => (v / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) + ' 万';
  const KPI_FIELDS = [
    { f: 'return_rate', t: '年退货率', fmt: (k) => k.return_rate.toFixed(2) + '%', sub: '全年平均' },
    { f: 'gmv_year', t: '年 GMV', fmt: (k) => fmtWan(k.gmv_year), sub: '全年成交' },
    { f: 'loss_total', t: '退货损失', fmt: (k) => fmtWan(k.loss_total), sub: '含物流与折价' },
    { f: 'orders_year', t: '年订单', fmt: (k) => k.orders_year.toLocaleString(), sub: '全年单量' },
    { f: 'aov', t: '客单价', fmt: (k) => '¥' + k.aov.toFixed(2), sub: '件均成交' },
    { f: 'reviews', t: '评论量', fmt: (k) => k.reviews.toLocaleString(), sub: '累计评论' },
  ];
  const DEFAULT_LAYOUT = [
    { id: 'ret', kind: 'kpi', field: 'return_rate', size: 1, chart: 'num' },
    { id: 'gmv', kind: 'kpi', field: 'gmv_year', size: 1, chart: 'num' },
    { id: 'loss', kind: 'kpi', field: 'loss_total', size: 1, chart: 'num' },
    { id: 'anom', kind: 'anomaly', size: 1, chart: 'num' },
    { id: 'trend', kind: 'trend', size: 4, chart: 'line' },
    { id: 'cat', kind: 'cat', size: 2, chart: 'bar' },
    { id: 'cause', kind: 'cause', size: 2, chart: 'ring' },
  ];
  let boardData = null;
  let boardEl = null;

  function loadLayout() {
    try { const s = JSON.parse(localStorage.getItem(BOARD_LS)); if (Array.isArray(s) && s.length) return s; } catch (_) {}
    return DEFAULT_LAYOUT.map((w) => ({ ...w }));
  }
  function saveLayout(l) { try { localStorage.setItem(BOARD_LS, JSON.stringify(l)); } catch (_) {} }

  /* ---- SVG 图表 ---- */
  function svgTrend(d, mode) {
    const s = d.series, W = 640, H = 240, P = 30;
    if (!s || s.length < 2) return '<div class="gb-empty">暂无数据</div>';
    const lo = Math.min(...s.map(p => p.rr), d.baseline.band_low) - .3;
    const hi = Math.max(...s.map(p => p.rr), d.baseline.band_high) + .3;
    const X = (i) => P + i * (W - 2 * P) / (s.length - 1);
    const Y = (v) => H - P - (v - lo) * (H - 2 * P) / (hi - lo || 1);
    let g = `<rect x="${P}" y="${Y(d.baseline.band_high)}" width="${W - 2 * P}" height="${Y(d.baseline.band_low) - Y(d.baseline.band_high)}" fill="rgba(35,104,114,.08)" rx="4"/>`;
    if (mode === 'bar') {
      s.forEach((p, i) => {
        const bw = (W - 2 * P) / s.length - 3;
        const y = Y(Math.max(p.rr, d.baseline.mean_rr)), h = Math.abs(Y(p.rr) - Y(d.baseline.mean_rr));
        g += `<rect x="${X(i) - bw / 2}" y="${y}" width="${bw}" height="${Math.max(h,1.5)}" rx="2.5" fill="${p.is_anomaly ? 'rgba(196,72,66,.85)' : 'rgba(35,104,114,.55)'}"/>`;
      });
    } else {
      g += `<polyline points="${s.map((p, i) => X(i) + ',' + Y(p.rr)).join(' ')}" fill="none" stroke="#236872" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
      s.forEach((p, i) => {
        if (p.is_anomaly) g += `<circle cx="${X(i)}" cy="${Y(p.rr)}" r="4.5" fill="#c44842" stroke="#fff" stroke-width="1.5"/>`;
      });
    }
    [0, 8, 16, 24, 32, 40, 48, 51].forEach((i) => {
      if (s[i]) g += `<text x="${X(i)}" y="${H - 8}" font-size="11" fill="rgba(31,50,56,.45)" text-anchor="middle" font-family="var(--mono)">W${s[i].week}</text>`;
    });
    const ml = (lo + hi) / 2;
    g += `<text x="${W - P}" y="${Y(ml) - 6}" font-size="11" fill="rgba(31,50,56,.5)" text-anchor="end">均值 ${ml.toFixed(2)}%</text>`;
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">${g}</svg>`;
  }

  function ringChart(items, centerLabel) {
    const total = items.reduce((a, b) => a + b.v, 0) || 1;
    const C = 2 * Math.PI * 70;
    let off = 0, seg = '';
    const palette = ['#236872', '#4f97a1', '#8f6d2c', '#ab4a48', '#5b7a94'];
    items.forEach((it, i) => {
      const frac = it.v / total;
      seg += `<circle r="70" cx="110" cy="110" fill="none" stroke="${palette[i % palette.length]}" stroke-width="26" stroke-dasharray="${(frac * C).toFixed(1)} ${C.toFixed(1)}" stroke-dashoffset="${(-off * C).toFixed(1)}" transform="rotate(-90 110 110)"/>`;
      off += frac;
    });
    return `<div class="gb-ring"><svg viewBox="0 0 220 220" style="width:190px;height:190px">${seg}` +
      `<text x="110" y="106" text-anchor="middle" font-size="15" fill="rgba(31,50,56,.55)">${centerLabel}</text>` +
      `<text x="110" y="128" text-anchor="middle" font-size="19" font-weight="700" fill="#1f3238">${total >= 10000 ? fmtWan(total) : total.toLocaleString()}</text></svg>` +
      `<div class="gb-ring-lg">` + items.map((it, i) =>
        `<span class="lg"><i style="background:${palette[i % palette.length]}"></i>${it.k} <b>${(it.v / total * 100).toFixed(1)}%</b></span>`).join('') +
      `</div></div>`;
  }

  function barRows(items, fmt) {
    const max = Math.max(...items.map(i => i.v), 0.0001);
    return items.map((it) =>
      `<div class="gb-bar-row"><span class="l">${it.k}</span>` +
      `<span class="track"><i style="width:${(it.v / max * 100).toFixed(1)}%"></i></span>` +
      `<span class="v">${fmt ? fmt(it.v) : it.v}</span></div>`).join('');
  }

  function renderWidget(w, d) {
    const k = d.kpi;
    let title = '', body = '';
    const chartTypes = { trend: ['line', 'bar'], cat: ['bar', 'ring'], cause: ['ring', 'bar'], anomaly: ['num', 'list'] };
    if (w.kind === 'kpi') {
      const f = KPI_FIELDS.find(x => x.f === w.field) || KPI_FIELDS[0];
      title = f.t;
      body = `<div class="gb-num"><span class="v">${f.fmt(k)}</span><span class="s">${f.sub}</span></div>`;
    } else if (w.kind === 'trend') {
      title = '退货率 · 52 周走势';
      body = svgTrend(d, w.chart);
    } else if (w.kind === 'cat') {
      title = '类目对比';
      const items = d.by_category.map(c => ({ k: c.category, v: c.return_rate }));
      body = w.chart === 'ring'
        ? ringChart(items, '平均退货率')
        : barRows(items, (v) => v.toFixed(2) + '%');
    } else if (w.kind === 'cause') {
      title = '差评原因构成';
      const items = d.causes.slice(0, 5).map(c => ({ k: c.cause, v: c.n }));
      body = w.chart === 'ring'
        ? ringChart(items, '差评条数')
        : barRows(items, (v) => v.toLocaleString() + ' 条');
    } else if (w.kind === 'anomaly') {
      title = '需解释的周';
      const n = d.anomalies.length;
      if (w.chart === 'num') {
        body = `<div class="gb-num"><span class="v ${n ? 'warn' : 'good'}">${n}<small> / ${k.weeks} 周</small></span>` +
          `<span class="s">${n ? d.anomalies.map(a => 'W' + a.week).slice(0, 6).join(' · ') : '暂无异常周'}</span></div>`;
      } else {
        body = n
          ? `<div class="gb-chips">${d.anomalies.map(a => `<span class="chip" style="background:rgba(196,72,66,.1);color:#ab4a48">W${a.week} · ${a.rr.toFixed(2)}%</span>`).join('')}</div>`
          : '<div class="gb-empty">52 周全部落在正常波动带内</div>';
      }
    }
    const types = chartTypes[w.kind];
    const ctrl = `
      <span class="gb-ctrl">
        ${types ? `<button data-act="chart" data-id="${w.id}" title="切换图表类型">◑ ${w.chart === 'line' ? '折线' : w.chart === 'bar' ? '柱状' : w.chart === 'ring' ? '环形' : '数字'}</button>` : ''}
        <button data-act="size" data-id="${w.id}" title="调整大小">⤢ ${{ 1: '小', 2: '中', 4: '大' }[w.size]}</button>
        <button data-act="del" data-id="${w.id}" title="移除">✕</button>
      </span>`;
    return `<div class="gb-w s${w.size}" data-id="${w.id}"><div class="gb-w-h"><span class="t">${title}</span>${ctrl}</div>${body}</div>`;
  }

  function renderBoard() {
    if (!boardEl) return;
    const layout = loadLayout();
    const addMenu = [
      ...KPI_FIELDS.map(f => `<button data-add="kpi:${f.f}">＋ ${f.t}</button>`),
      `<button data-add="trend">＋ 走势图</button>`,
      `<button data-add="cat">＋ 类目对比</button>`,
      `<button data-add="cause">＋ 原因构成</button>`,
      `<button data-add="anomaly">＋ 异常周</button>`,
    ].join('');
    boardEl.innerHTML = `
      <div class="gb-head">
        <span class="gb-title">工作台</span>
        <span class="gb-tip">悬停组件可调整大小与图表类型</span>
        <span class="gb-actions">
          <button data-act="add">＋ 添加组件</button>
          <button data-act="reset">恢复默认</button>
          <button data-act="close">完成</button>
        </span>
      </div>
      <div class="gb-addmenu" hidden>${addMenu}</div>
      <div class="gb-grid">${layout.map((w) => renderWidget(w, boardData)).join('')}</div>`;
  }

  function boardDelegate(e) {
    const btn = e.target.closest('[data-act], [data-add]');
    if (!btn || !boardEl) return;
    const layout = loadLayout();
    if (btn.dataset.add) {
      const [kind, field] = btn.dataset.add.split(':');
      layout.push({ id: kind + (field || '') + Date.now(), kind, field, size: kind === 'trend' ? 4 : 2, chart: kind === 'cause' ? 'ring' : kind === 'trend' ? 'line' : kind === 'cat' ? 'bar' : 'num' });
    } else {
      const act = btn.dataset.act, id = btn.dataset.id;
      if (act === 'close') { closeBoard(); return; }
      if (act === 'reset') { saveLayout(null); renderBoard(); return; }
      if (act === 'add') { const m = boardEl.querySelector('.gb-addmenu'); m.hidden = !m.hidden; return; }
      const w = layout.find(x => x.id === id);
      if (!w) return;
      if (act === 'del') layout.splice(layout.indexOf(w), 1);
      if (act === 'size') { w.size = w.size === 1 ? 2 : w.size === 2 ? 4 : 1; }
      if (act === 'chart') {
        const seq = { trend: ['line', 'bar'], cat: ['bar', 'ring'], cause: ['ring', 'bar'], anomaly: ['num', 'list'] }[w.kind];
        w.chart = seq[(seq.indexOf(w.chart) + 1) % seq.length];
      }
    }
    saveLayout(layout);
    renderBoard();
  }

  function openBoard() {
    if (boardEl) { closeBoard(); return; }
    boardEl = document.createElement('div');
    boardEl.id = 'glass-board';
    boardEl.innerHTML = '<div class="gb-loading">正在载入店况数据…</div>';
    document.body.appendChild(boardEl);
    document.documentElement.style.overflow = 'hidden';
    fetch('/api/pages/dashboard').then(r => r.json()).then((d) => {
      if (!boardEl) return;
      boardData = d;
      renderBoard();
      boardEl.addEventListener('click', boardDelegate);
    }).catch(() => { if (boardEl) boardEl.innerHTML = '<div class="gb-loading">数据载入失败，请确认服务已启动</div>'; });
  }
  function closeBoard() {
    if (!boardEl) return;
    boardEl.remove(); boardEl = null;
    document.documentElement.style.overflow = '';
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeBoard(); });

  function ensureBoardBtn() {
    /* 已按要求下架：不做新页面，只保留原有总览页的样式精修 */
    const old = document.getElementById('glass-board-btn');
    if (old) old.remove();
    return;
  }

  /* ================================================================ 装配 */
  function followRenders() {
    injectDrop();
    enhancePersona();
    enhanceButterfly();
    tagTrendCard();
    enhanceDashGreeting();
    ensureBoardBtn();
    if (!isChatPage()) ensureExpButtons();
  }

  let obsTimer = null;
  const obs = new MutationObserver(() => {
    clearTimeout(obsTimer);
    obsTimer = setTimeout(followRenders, 120);
  });

  function boot() {
    initLoginGate();
    document.body.appendChild(dock);
    document.body.appendChild(cmdk);
    fetch('/api/pages/nav').then((r) => r.json()).then((n) => {
      NAV = n;
      buildDock();
      renderCmdk('');
    }).catch(() => { buildDock(); });
    buildDock();
    followRenders();
    obs.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('hashchange', () => {
      closeCmdk();
      collapseAll(true);
      syncDock(false);
      setTimeout(followRenders, 60);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

/* ===========================================================================
 * ⑦ 退货原因 · 诊断矩阵 + 动作闭环（策划方案页 · v3）
 * 左列 = 退货原因卡（损失金额/条数/占比，CountUp 入场）
 * 中列 = 影响强弱矩阵（履约损失/评价风险/体验损伤/复购影响，深浅=强弱）
 * 右列 = 优化动作药丸（点击打勾 = 标记已落地）
 * 实线弧（原因→矩阵）依次 draw-in + 粒子光流；虚线弧（矩阵→动作）行军动画；
 * 悬停/自动演示联动高亮整条「原因 → 影响 → 动作」链路。
 * 数据从页面条形图 DOM 读取，动作映射取自后端 action_playbook。
 * =========================================================================== */
(function () {
  'use strict';
  const CAUSES = [
    { key: '破损', c: '#8B72D8', hi: '#A98BD8', acts: [
      ['泡沫箱+冰袋+防撞隔板', '生鲜改用"泡沫箱 + 冰袋 + 防撞隔板"，按品类定温区'],
      ['破损纳入承运商考核', '把破损率纳入承运商考核，按月结算扣罚'],
      ['2小时主动补偿', '破损订单 2 小时内主动补偿，不等客户投诉']] },
    { key: '描述', c: '#5B8FD8', hi: '#7FA8E8', acts: [
      ['主图同色卡比对', '主图与实物做同色卡比对，杜绝滤镜色差'],
      ['标题去无效形容词', '标题去掉"显瘦/高端"等无法验证的形容词'],
      ['详情页承诺清单', '建立"详情页承诺"清单，发货前逐条核对']] },
    { key: '材质', c: '#4FA97C', hi: '#6BC49A', acts: [
      ['微距实拍 3 组', '详情页补 3 组微距实拍：面料纹理、缝线、水洗标'],
      ['洗涤说明前置预期', '把"起球/掉色"写进洗涤说明，前置管理预期'],
      ['抽检留样锁定批次', '抽检同批次留样，锁定问题批次并下架']] },
    { key: '价值', c: '#D89B5E', hi: '#E8B585', acts: [
      ['价格价值对比图', '详情页首屏放"这个价格你买到什么"的对比图'],
      ['赠品权益显性化', '把赠品/服务权益显性化（现在多数藏在详情页底部）'],
      ['预期落差回访', '对"觉得不值"的退货用户做回访问卷，定位预期落差']] },
    { key: '尺码', c: '#4FA3A0', hi: '#58A29B', acts: [
      ['尺码器+试穿视频', '详情页首屏加"尺码选择器 + 真人试穿视频"'],
      ['三围数据表格', '把各尺码的实际三围数据做成表格'],
      ['换码不退款券', '对已退货用户推"换码不退款"券，把退货转成换货']] },
    { key: '时效', c: '#C27BA8', hi: '#D495C4', acts: [
      ['分仓压时效至48h', '按区域分仓，华东华南承诺时效 72h → 48h'],
      ['发货扫码复核', '发货前加一道"扫码复核"'],
      ['超时补偿券', '超时订单自动推送延迟说明 + 补偿券']] },
  ];
  const COLS = ['履约损失', '评价风险', '体验损伤', '复购影响'];
  /* 影响强弱矩阵：每类原因对四个维度的影响度 0~1（深浅上屏） */
  const IMP = {
    '破损': [.9, .35, .8, .55],
    '描述': [.3, .9, .75, .6],
    '材质': [.2, .8, .85, .7],
    '价值': [.15, .45, .9, .75],
    '尺码': [.1, .4, .7, .45],
    '时效': [.95, .3, .6, .5],
  };

  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function findBars() {
    /* 依据：≥4 行文本命中原因关键词，足够唯一，不再依赖旧页面元素 */
    for (const b of document.querySelectorAll('.bi-bars')) {
      const rows = [...b.querySelectorAll('.bi-bar-row')];
      if (rows.length < 4) continue;
      let hit = 0; rows.forEach(r => { if (CAUSES.some(c => (r.textContent || '').includes(c.key))) hit++; });
      if (hit >= 4) return { bars: b, rows };
    }
    return null;
  }
  function readRows(rows) {
    const out = [];
    rows.forEach(r => {
      const text = r.textContent || '';
      const def = CAUSES.find(c => text.includes(c.key));
      if (!def) return;
      const m = text.match(/([\d.]+)\s*万/), n = text.match(/([\d.]+)\s*条/), p = text.match(/([\d.]+)%/);
      const l = r.querySelector('.bi-bar-l');
      out.push({ def, row: r, loss: m ? parseFloat(m[1]) : 1,
        share: p ? parseFloat(p[1]) : 0,
        label: (l && l.textContent || def.full || def.key || '').replace(/…$/, ''),
        meta: (n ? n[1] + '条' : '') + (p ? ' · ' + p[1] + '%' : '') });
    });
    return out.sort((a, b) => b.loss - a.loss);
  }
  /* 放射诊断地图：中心大圆（第 N 周差评诊断）+ 6 条辐射分支（粗细=损失占比）
     + 末端原因节点（损失/占比）挂 2-3 个核心动作标签；颜色按原因分类区分。
     数据仍从页面条形图 DOM 读取（真实数据）；点击节点联动原条形图行。
     运行异常记录到 window.__grErr（F12 查看），不炸页面。 */
  function renderRadial() {
    try { renderRadial0(); } catch (e) {
      try { (window.__grErr = window.__grErr || []).push(String((e && e.message) || e)); } catch (_) {}
    }
  }
  function renderRadial0() {
    /* 双版式：预警方案页 = 大版；态势全景「退货原因结构」= 小型紧凑版。
       其它页面不渲染。占比一律取自页面条形图文本（与当前显示一致）。 */
    const small = !!document.querySelector('.pg-kpi-row');
    if (!small && !/策划方案/.test(document.body.textContent || '')) return;
    const found = findBars();
    if (!found) return;
    const { bars, rows } = found;
    const causes = readRows(rows);
    if (causes.length < 4) return;
    let wrap = bars.parentElement.querySelector('.glass-sankey');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'glass-sankey';
      bars.parentElement.insertBefore(wrap, bars);
    }
    const sig = causes.map(c => c.loss + c.label).join('|') + (small ? '#rs1' : '#r1');
    if (wrap.dataset.sig === sig && wrap.firstChild) { bars.style.display = 'none'; wrap.style.display = ''; return; }
    wrap.dataset.sig = sig;
    wrap.innerHTML = '';

    const es = s => String(s).replace(/[&<>"]/g, ch =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    const W = small ? 480 : 640, H = small ? 500 : 580;
    const CX = W / 2, CY = H / 2 + 4, R = small ? 50 : 84;
    /* 位置槽：降序原因左右对称铺开（0右上 1左上 2右中 3左中 4右下 5左下）。
       大版上排 y=136：压在右侧动作明细表（页面顶部 ~170px 内有行文字）下方，避免挡字 */
    const SLOTS = [{ s: 1, r: 0 }, { s: -1, r: 0 }, { s: 1, r: 1 }, { s: -1, r: 1 }, { s: 1, r: 2 }, { s: -1, r: 2 }];
    const rowYs = small ? [72, CY, 430] : [136, CY, 468];
    wrap.classList.add('glass-radial');
    if (small) wrap.classList.add('sm');   /* classList.add 不接受含空格的 token */
    wrap.style.cssText = 'height:' + H + 'px;min-width:' + W + 'px;';

    const svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, 'class': 'gr-svg' }, wrap);
    const cg = svgEl('defs', {}, svg);
    const g1 = svgEl('linearGradient', { id: 'gr-cg', x1: 0, y1: 0, x2: 1, y2: 1 }, cg);
    svgEl('stop', { offset: 0, 'stop-color': '#FFFFFF' }, g1);
    svgEl('stop', { offset: 1, 'stop-color': '#EBE3F8' }, g1);
    /* 中心大圆：虚线光环 + 渐变主圆 + 标题（周数从页面标题读取） */
    svgEl('circle', { cx: CX, cy: CY, r: R + 15, fill: 'none', stroke: '#C9BCE8',
      'stroke-width': 1.3, 'stroke-dasharray': '2 6', 'class': 'gr-ring' }, svg);
    svgEl('circle', { cx: CX, cy: CY, r: R, fill: 'url(#gr-cg)', 'class': 'gr-core' }, svg);
    const wk = (document.body.textContent.match(/第\s*(\d+)\s*周/) || [])[1] || '44';
    const total = causes.reduce((a, c) => a + (c.share || 0), 0);
    const t1 = svgEl('text', { x: CX, y: CY - (small ? 16 : 24), 'text-anchor': 'middle', 'class': 'gr-t1' }, svg);
    t1.textContent = '第 ' + wk + ' 周';
    const t2 = svgEl('text', { x: CX, y: CY + (small ? 3 : 4), 'text-anchor': 'middle', 'class': 'gr-t2' }, svg);
    t2.textContent = '差评诊断';
    const t3 = svgEl('text', { x: CX, y: CY + (small ? 19 : 28), 'text-anchor': 'middle', 'class': 'gr-t3' }, svg);
    t3.textContent = causes.length + ' 大原因 · 合计 ' + total.toFixed(1) + '%';

    /* 联动高亮：悬停分支/节点 → 该链路保持，其余淡出 */
    let hoverDef = null;
    const pieces = [];
    function hl(idx) {
      pieces.forEach((p, j) => {
        const on = idx == null || j === idx;
        p.path.style.opacity = on ? '' : '.15';
        if (p.endC) p.endC.style.opacity = on ? '' : '.15';
        p.node.style.opacity = on ? '' : '.28';
        p.path.style.strokeWidth = on ? (idx == null ? p.baseW : +p.baseW + 1.6) : p.baseW;
      });
    }

    causes.forEach((c, i) => {
      const slot = SLOTS[i] || SLOTS[i % 6];
      /* 小版：分支末端伸进节点背后（节点白底盖住线端）；大版：止于节点左侧 */
      const nx = small ? CX + slot.s * (R + 96) : CX + slot.s * (R + 34);
      const ey = rowYs[slot.r];
      const sy = CY + (ey - CY) * 0.14;
      const sx = CX + slot.s * (R - 6);
      const sw = +(small ? 1.8 + (c.share || 0) * 0.09 : 3 + (c.share || 0) * 0.34).toFixed(1);  /* 粗细 = 占比 */
      const path = svgEl('path', {
        d: 'M ' + sx.toFixed(1) + ' ' + sy.toFixed(1) +
          ' C ' + (CX + slot.s * (R + (small ? 40 : 64))).toFixed(1) + ' ' + sy.toFixed(1) + ', ' +
          (nx - slot.s * (small ? 48 : 72)).toFixed(1) + ' ' + ey + ', ' + nx.toFixed(1) + ' ' + ey.toFixed(1),
        fill: 'none', stroke: c.def.c, 'stroke-width': sw, 'stroke-linecap': 'round',
        'class': 'gr-branch',
      }, svg);
      const endC = small ? null : svgEl('circle', { cx: nx.toFixed(1), cy: ey.toFixed(1),
        r: Math.min(6.5, 2.6 + (c.share || 0) * .13).toFixed(1), fill: c.def.c, 'class': 'gr-enddot' }, svg);
      /* 末端原因节点：名称 + 真实损失/占比 + 核心动作标签（小版 2 个，大版 3 个） */
      const node = document.createElement('div');
      node.className = 'gr-node';
      node.style.cssText = (slot.s > 0
        ? (small ? 'left:' + (W - 146) + 'px;' : 'left:' + (nx + 16) + 'px;')
        : (small ? 'left:6px;' : 'right:' + (W - nx + 16) + 'px;')) +
        'top:' + (ey - (small ? 44 : 36)) + 'px;animation-delay:' + (200 + i * 110) + 'ms;';
      const acts = small ? c.def.acts.slice(0, 2) : c.def.acts;
      node.innerHTML =
        '<div class="gr-name" style="color:' + c.def.c + '">' + es(c.label) + '</div>' +
        '<div class="gr-meta"><b>' + c.loss.toFixed(1) + '</b> 万' + (c.meta ? ' · ' + es(c.meta) : '') + '</div>' +
        '<div class="gr-acts">' + acts.map(a =>
          '<span style="--c:' + c.def.c + '" title="' + es(a[1]) + '（' + es(c.label) + '）"><i></i>' + es(a[0]) + '</span>').join('') +
        '</div>';
      wrap.appendChild(node);
      const on = () => { hoverDef = c.def; hl(i); };
      const off = () => { hoverDef = null; hl(null); };
      const hovers = endC ? [node, path, endC] : [node, path];
      hovers.forEach(el => {
        el.addEventListener('mouseenter', on);
        el.addEventListener('mouseleave', off);
      });
      node.addEventListener('click', () => { if (c.row) c.row.click(); });
      node.style.cursor = 'pointer';
      /* 入场：分支 draw-in 依次描绘 */
      const len = path.getTotalLength();
      path.style.strokeDasharray = String(len);
      path.style.strokeDashoffset = String(len);
      setTimeout(() => {
        path.style.transition = 'stroke-dashoffset .9s cubic-bezier(.4,0,.2,1) ' + (i * 110) + 'ms';
        path.style.strokeDashoffset = '0';
      }, 60);
      pieces.push({ path, endC, node, baseW: sw, def: c.def });
    });

    /* 粒子光流（大版专属）：光点沿分支从中心流向末端 */
    if (!small) {
    const pg = svgEl('g', { 'class': 'gr-ps' }, svg);
    const particles = [];
    pieces.forEach(p => {
      const len = p.path.getTotalLength();
      for (let k = 0; k < 2; k++) {
        particles.push({ el: svgEl('circle', { r: 2.1, fill: p.def.hi }, pg), path: p.path, len, t: k / 2, def: p.def });
      }
    });
    let last = performance.now();
    setInterval(() => {
      const now = performance.now();
      const dt = Math.min(60, now - last); last = now;
      particles.forEach(p => {
        const on = hoverDef && p.def === hoverDef;
        const speed = hoverDef ? (on ? .3 : 0) : .11;
        if (speed) p.t = (p.t + dt / 1000 * speed) % 1;
        const pt = p.path.getPointAtLength(p.t * p.len);
        p.el.setAttribute('cx', pt.x.toFixed(1));
        p.el.setAttribute('cy', pt.y.toFixed(1));
        p.el.setAttribute('opacity', hoverDef ? (on ? '.95' : '.03') : '.55');
      });
    }, 33);
    }
    bars.style.display = 'none';
  }
  let tmr = null;
  new MutationObserver(() => { clearTimeout(tmr); tmr = setTimeout(renderRadial, 200); })
    .observe(document.body, { childList: true, subtree: true });
  renderRadial();
})();

/* ===========================================================================
 * ⑧⑨ 总览补强 · 关键结论卡 + 副标题文案 + 原生 title 清除
 * 关键结论卡插到总览页底部双栏（.pg-2col）最后，落在左下角；
 * 类目横向对比 / 退货原因结构补副标题；行上原生 title 移除
 * （悬浮详情由 .bi-tip 富浮层呈现，避免黑框重复弹）。
 * =========================================================================== */
(function () {
  'use strict';
  const VERDICT = [
    { d: 'd1', t: '破损、变质污损是最大损失来源，优先整改' },
    { d: 'd2', t: '描述不符、做工瑕疵是高频退货问题' },
    { d: 'd3', t: '尺码、时效类占比较低，持续跟踪监控' },
  ];
  const NOTES = {
    '类目横向对比': '点选一行筛选对应类目，下方图表联动更新',
    '退货原因结构': '条长为该原因损失金额占比，点击查看对应整改动作',
  };
  let tmr = null;
  function patch() {
    const two = document.querySelector('.pg-2col');
    if (two && !two.querySelector('.glass-verdict')) {
      const d = document.createElement('div');
      d.className = 'pg-card glass-verdict';
      d.innerHTML =
        `<div class="pg-card-h"><div class="pg-card-t">关键结论</div></div>` +
        `<ul class="gv-list">` +
        VERDICT.map((v) => `<li><i class="dot ${v.d}"></i>${v.t}</li>`).join('') +
        `</ul>`;
      two.insertBefore(d, two.lastElementChild.nextSibling);
    }
    document.querySelectorAll('.pg-card-h').forEach((h) => {
      const t = h.querySelector('.pg-card-t');
      if (!t || h.querySelector('.glass-note')) return;
      const txt = NOTES[t.textContent.trim()];
      if (txt && !h.querySelector('.pg-card-note')) {
        h.insertAdjacentHTML('beforeend', `<span class="pg-card-note glass-note">${txt}</span>`);
      }
    });
    document.querySelectorAll('.bi-bars [title]').forEach((el) => el.removeAttribute('title'));
  }
  new MutationObserver(() => { clearTimeout(tmr); tmr = setTimeout(patch, 150); })
    .observe(document.body, { childList: true, subtree: true });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', patch);
  } else {
    patch();
  }
})();

/* ===========================================================================
 * ⑩ KPI 数字滚动 · CountUp（各页 .pg-kpi-row 的大数字格）
 * 数字从 0 快速递增、后半段缓慢趋近（easeOutBack 轻微回弹），
 * 定格后加 .gcu-done：深紫高亮 + 微光；只动数字文本，不动单位/副文字/图表。
 * 数值随筛选变化时（dataset 签名不同）自动重滚一次。
 * =========================================================================== */
(function () {
  'use strict';
  const DUR = 1500, STAG = 130;
  /* easeOutBack（c1 调小 = 约 4% 回弹）：前段陡升、后段缓近、轻微过冲回落 */
  const ease = (t) => { const c1 = 0.9, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };

  function countUp(el, idx) {
    /* 数字 = .pg-stat-v 里第一个文本节点（<small> 单位等其余内容不动） */
    const tn = el.firstChild;
    if (!tn || tn.nodeType !== 3) { el.dataset.gcu = el.textContent; return; }
    const text = tn.textContent;
    const m = text.match(/^([\d,]+(?:\.\d+)?)([\s\S]*)$/);
    if (!m) { el.dataset.gcu = el.textContent; return; }
    const numStr = m[1], suffix = m[2];
    const target = parseFloat(numStr.replace(/,/g, ''));
    if (!isFinite(target)) { el.dataset.gcu = el.textContent; return; }
    const dec = (numStr.split('.')[1] || '').length;
    const group = numStr.includes(',');
    el.dataset.gcu = el.textContent;   /* 签名 = 最终全文，滚完后与之相等即跳过 */
    el.dataset.gcuRun = '1';           /* 动画锁：中途扫描不重启 */

    /* 【不可见时直接落定最终值，绝不留在 0】
       rAF 在三种情况下会被节流或完全不触发：后台标签页、
       prefers-reduced-motion、以及无头浏览器截图。
       而 step(0) 的第一次调用把 k 算成 0，会立刻把数字写成 "0.00"。
       两者一叠加，用户切走再切回来就看到「退货率 0.00%、订单 0 单」——
       一个纯粹的显示层假象，却比"没有数据"更误导：它看起来像一个
       干净得反常的经营周。所以先判断可见性，不可见就直接写终值。 */
    const settle = () => {
      tn.textContent = numStr + suffix;
      el.classList.add('gcu-done');
      delete el.dataset.gcuRun;
    };
    if (document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      settle(); return;
    }

    const fmt = (v) => {
      const s = dec ? v.toFixed(dec) : String(Math.round(v));
      return group ? s.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : s;
    };
    const t0 = performance.now() + 200 + idx * STAG;
    /* 【留一条兜底：动画没跑完就把最终值写回去】
       即使可见，rAF 也可能因为主线程被长任务占满而长时间不回调
       （本项目首屏要拉 30~50KB JSON 并渲染 SVG）。
       1.6 × DUR 之后无条件落定，宁可少一段动画，也不要一个错的 0。 */
    const guard = setTimeout(settle, DUR * 1.6 + 400 + idx * STAG);
    (function step(now) {
      const k = Math.min(1, Math.max(0, ((now || 0) - t0) / DUR));
      tn.textContent = fmt(Math.max(0, target * ease(k))) + suffix;
      if (k < 1) requestAnimationFrame(step);
      else { clearTimeout(guard); settle(); }
    })(0);
  }

  let tmr = null;
  function scan() {
    document.querySelectorAll('.pg-kpi-row .pg-stat-v').forEach((el, idx) => {
      if (el.dataset.gcuRun === '1') return;           /* 动画进行中，勿动 */
      if (el.dataset.gcu === el.textContent) return;   /* 已滚过且数值未变 */
      el.classList.remove('gcu-done');
      countUp(el, idx);
    });
  }
  new MutationObserver(() => { clearTimeout(tmr); tmr = setTimeout(scan, 250); })
    .observe(document.body, { childList: true, subtree: true, characterData: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
})();

/* ===========================================================================
 * ⑫ 类目横向对比 · 双条并列（总览页）
 * 给该卡加 .gcb-card（CSS 把叠条重排为「实心条=当前 / 浅灰条=近12周基准」）；
 * 行错峰入场（--gi）、条形从 0 伸展；右列数字 countUp；
 * 当前>基准 = .g-worse（红柔光），<基准 = .g-better（绿柔光）。
 * 点击筛选 / 富提示 / 键盘可达全部沿用 React 原行，不接管事件。
 * =========================================================================== */
(function () {
  'use strict';
  const DUR = 1100, STAG = 90;
  const ease = (t) => 1 - Math.pow(1 - t, 3);

  function countUpMain(el, idx) {
    const tn = el.firstChild;
    if (!tn || tn.nodeType !== 3) { el.dataset.gcb = el.textContent; return; }
    const text = tn.textContent;
    const m = text.match(/^([^\d]*)([\d,]+(?:\.\d+)?)([\s\S]*)$/);
    if (!m) { el.dataset.gcb = el.textContent; return; }
    const pre = m[1], numStr = m[2], suf = m[3];
    const target = parseFloat(numStr.replace(/,/g, ''));
    if (!isFinite(target)) { el.dataset.gcb = el.textContent; return; }
    const dec = (numStr.split('.')[1] || '').length;
    const group = numStr.includes(',');
    el.dataset.gcb = text;
    el.dataset.gcbRun = '1';
    const fmt = (v) => {
      const s = dec ? v.toFixed(dec) : String(Math.round(v));
      return group ? s.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : s;
    };
    const t0 = performance.now() + 60 + idx * STAG;
    /* 同 countUp：不可见 / 降动效 时不跑动画，直接落定终值，
       否则会停在 0（= 一个假的"这一项是 0"）。 */
    const settle = () => {
      tn.textContent = pre + numStr + suf;
      delete el.dataset.gcbRun;
    };
    if (document.hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      settle(); return;
    }
    const guard = setTimeout(settle, DUR * 1.6 + 400 + idx * STAG);
    (function step(now) {
      const k = Math.min(1, Math.max(0, ((now || 0) - t0) / DUR));
      tn.textContent = pre + fmt(target * ease(k)) + suf;
      if (k < 1) requestAnimationFrame(step);
      else { clearTimeout(guard); settle(); }
    })(0);
  }

  let tmr = null;
  function patch() {
    let card = null;
    document.querySelectorAll('.pg-card').forEach((c) => {
      const t = c.querySelector('.pg-card-t');
      if (t && t.textContent.trim() === '类目横向对比') card = c;
    });
    if (!card) return;
    card.classList.add('gcb-card');
    card.querySelectorAll('.bi-bar-row').forEach((row, i) => {
      row.style.setProperty('--gi', i);
      /* 恶化 / 好转：比较实心条与基准条的归一宽度（同一分母，可直接比） */
      const fill = row.querySelector('.bi-bar-f:not(.is-ghost)');
      const ghost = row.querySelector('.bi-bar-f.is-ghost');
      row.classList.remove('g-worse', 'g-better');
      if (fill && ghost) {
        const w = parseFloat(fill.style.width) || 0;
        const g = parseFloat(ghost.style.width) || 0;
        if (g > 0) {
          if (w > g + .05) row.classList.add('g-worse');
          else if (w < g - .05) row.classList.add('g-better');
        }
      }
      /* 右列主数字 countUp（数值变化时重滚，如切换指标标签） */
      const main = row.querySelector('.bi-bar-main');
      if (main && main.dataset.gcbRun !== '1' && main.dataset.gcb !== main.textContent) {
        countUpMain(main, i);
      }
    });
  }
  new MutationObserver(() => { clearTimeout(tmr); tmr = setTimeout(patch, 100); })
    .observe(document.body, { childList: true, subtree: true, characterData: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', patch);
  else patch();
})();

/* ===========================================================================
 * ⑬ 问一问 · 差评分析诊断驾驶舱（landing 专属 v2）
 * 版式：顶部导航条 → 面包屑+大标题+副标题 → 筛选条（时间下拉/类目/指标单选）
 *   → 主指标横幅（环比大数 + 发光趋势线）→ 三卡（异常摘要/关键原因排行/类目对比）
 *   → 右栏「筛选详情」面板；右下角 AI 角标。
 * 数据全部真实：/api/pages/dashboard + /api/pages/reviews（差评率）；
 * 「退款率」后端无字段，指标选项已移除（仅留防御分支）；右栏「筛选详情」为
 * 视觉面板（用户要求保留）。进入对话后 React 移除 .empty，布局自动恢复。
 * =========================================================================== */
(function () {
  'use strict';
  let dd = null, rv = null, loaded = false;
  let metric = '差评率', span = '本周', cat = null;   /* cat=null 即全店 */
  let wrap = null, ai = null;
  const ddCache = {};   /* 按类目缓存 dashboard 响应（后端同口径缓存，二次切换 0ms） */
  const pct1 = (v) => Number(v).toFixed(2) + '%';
  const esc2 = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* 类目联动在服务端做（后端原则：前端不许算数）：
     category 给定时 KPI/曲线/原因/异常周整页重算，by_category 恒为全量对照 */
  function loadCat(c) {
    const key = c || '_all';
    if (ddCache[key]) { dd = ddCache[key]; return Promise.resolve(); }
    const url = '/api/pages/dashboard' + (c ? '?category=' + encodeURIComponent(c) : '');
    return fetch(url).then((r) => r.json())
      .then((d) => { ddCache[key] = d; dd = d; })
      .catch(() => {});
  }

  function ensureData() {
    if (loaded) return Promise.resolve();
    loaded = true;
    return loadCat(null)
      .then(() => fetch('/api/meta').then((r) => r.json()).catch(() => null))
      .then((meta) => {
        const ent = (meta && meta.entities || [])[0];
        return ent ? fetch('/api/pages/reviews?entity=' + encodeURIComponent(ent))
          .then((r) => r.json()).then((d) => { rv = d; }).catch(() => {}) : null;
      });
  }

  /* 指标 → 序列。差评率只有全店口径（评论接口无类目参数），
     选中类目时如实降级；退货率用 dd.tail（已随类目由服务端重算） */
  function seriesData() {
    if (metric === '差评率') {
      if (cat) return null;
      if (rv && rv.weekly && rv.weekly.length >= 2)
        return rv.weekly.map((w) => w.neg_pct);
    }
    if (dd && dd.tail && dd.tail.length >= 2)
      return dd.tail.map((p) => p.rr);
    return null;
  }

  function deltaFor(series) {
    const a = series[series.length - 1];
    const b = span === '近4周' ? series[series.length - 4]
      : span === '近12周' ? series[0] : series[series.length - 2];
    if (b === undefined || !b) return null;
    return { pct: (a - b) / b * 100, last: a };
  }

  /* 卡头小方形线性图标（三卡统一，对齐参考图） */
  const CC_ICO = '<svg class="cc-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linecap="round"><rect x="2.6" y="2.6" width="10.8" height="10.8" rx="2.4"/>' +
    '<path d="M5.4 6.2h5.2M5.4 8.4h5.2M5.4 10.6h3"/></svg>';

  function sparkSVG(series) {
    const pts = series.slice(-12);
    if (pts.length < 2) return { html: '', mi: 0, left: 0, bottom: 0 };
    const W = 300, H = 64, P = 6;
    const min = Math.min(...pts), max = Math.max(...pts);
    const sp = (max - min) || 1e-9;
    const xy = pts.map((v, i) => [
      P + i * (W - 2 * P) / (pts.length - 1),
      H - P - (v - min) / sp * (H - 2 * P),
    ]);
    let mi = 0;
    pts.forEach((v, i) => { if (v > pts[mi]) mi = i; });
    const pk = xy[mi];
    const line = xy.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
    const area = 'M' + xy[0][0].toFixed(1) + ',' + (H - P) + ' L' +
      line.split(' ').join(' L') + ' L' + xy[xy.length - 1][0].toFixed(1) + ',' + (H - P) + ' Z';
    const dots = xy.slice(1, -1).map((p) =>
      `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="#8A7BC8" opacity=".55"/>`).join('');
    const last = xy[xy.length - 1];
    const html = `<svg class="cc-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <defs><linearGradient id="ccSparkG" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#8A7BC8" stop-opacity=".30"/>
        <stop offset="1" stop-color="#8A7BC8" stop-opacity="0"/></linearGradient></defs>
      <path d="${area}" fill="url(#ccSparkG)"/>
      <line x1="${pk[0].toFixed(1)}" y1="${pk[1].toFixed(1)}" x2="${pk[0].toFixed(1)}" y2="${H - P}"
        stroke="rgba(124,111,208,.4)" stroke-width="1" stroke-dasharray="2 3"/>
      <polyline points="${line}" fill="none" stroke="#7C6FD0" stroke-width="2.2"
        stroke-linecap="round" stroke-linejoin="round"/>
      ${dots}
      <circle cx="${pk[0].toFixed(1)}" cy="${pk[1].toFixed(1)}" r="4.4" fill="#FFF2F8"
        stroke="#E59CC0" stroke-width="1.8" filter="drop-shadow(0 0 6px rgba(229,156,192,.95))"/>
      <circle class="cc-dot" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4.5" fill="#7C6FD0"
        stroke="#fff" stroke-width="1.6" filter="drop-shadow(0 0 5px rgba(124,111,208,.8))"/>
    </svg>`;
    return { html, mi, left: pk[0] / W * 100, bottom: (H - pk[1]) / H * 100 };
  }

  /* 数字从 0 滚动到目标值（切换类目/指标/时间、首次载入时生效）。
     解析节点文本里的数值反向播放：带 % 的按小数滚动，整数按千分位滚动；
     解析不了的（— 之类）原样跳过 */
  function countUp(scope) {
    const dur = 700;
    scope.querySelectorAll('.cc-hero-v:not(.na), .cc-big, .cc-kv b, .ccr-v, .ccb-v b')
      .forEach((el) => {
        const m = el.textContent.trim().match(/^([+\-]?)([\d,]+(?:\.(\d+))?)(%)?$/);
        if (!m) return;
        const sign = m[1], target = parseFloat(m[2].replace(/,/g, '')),
              dec = m[3] ? m[3].length : 0, pct = m[4] || '';
        const t0 = performance.now();
        const step = (now) => {
          const p = Math.min((now - t0) / dur, 1);
          const e = 1 - Math.pow(1 - p, 3);   /* ease-out cubic */
          el.textContent = pct
            ? sign + (target * e).toFixed(dec) + '%'
            : sign + Math.round(target * e).toLocaleString();
          if (p < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
  }

  function render() {
    if (!wrap || !document.contains(wrap)) return;
    /* 类目数据未就绪：先渲染加载态，拉到后重渲染（后端整页重算，约 200-300ms，有缓存后 0ms） */
    if (!ddCache[cat || '_all']) {
      wrap.innerHTML = '<div class="cc-loading">正在加载 ' + esc2(cat || '全店') + ' 数据…</div>';
      loadCat(cat).then(render);
      return;
    }
    dd = ddCache[cat || '_all'];
    const catsList = (dd && dd.by_category || []).map((c) => esc2(c.category));
    const cats4 = (dd && dd.by_category || []).slice()
      .sort((a, b) => b.return_rate - a.return_rate).slice(0, 4);
    const maxRr = Math.max(...cats4.map((c) => Math.max(c.return_rate, c.rr_last12)), 1e-9);
    const causes = (dd && dd.causes || []).slice(0, 5);
    const causeMax = Math.max(...causes.map((c) => c.share), 1e-9);
    const t = dd && dd.tail, lastT = (t && t[t.length - 1]) || {};
    const neg = rv && rv.sentiment ? rv.sentiment.neg : null;
    const c0 = (dd && dd.causes || [])[0];

    /* 主横幅：左侧标题→大数→小字纵向排列，右侧大图；峰点挂 Tooltip 气泡 */
    const series = metric === '退款率' ? null : seriesData();
    let hero;
    if (!series) {
      const naMsg = (metric === '差评率' && cat)
        ? '差评率暂无类目级数据（评论仅全店口径），请切回全店或选退货率'
        : '该指标数据源暂未接入';
      hero = `<div class="cc-hero"><div class="cc-hero-k">
        <div class="cc-hero-t">${esc2(metric)}环比</div>
        <div class="cc-hero-v na">—</div>
        <div class="cc-hero-s">${esc2(naMsg)}</div></div></div>`;
    } else {
      const d = deltaFor(series);
      const up = d.pct >= 0;
      const spk = sparkSVG(series);
      hero = `<div class="cc-hero">
        <span class="cc-hero-tag">近12周趋势</span>
        <div class="cc-hero-k">
          <div class="cc-hero-t">${esc2(metric)}环比</div>
          <div class="cc-hero-v ${up ? 'up' : 'down'}">${up ? '+' : ''}${d.pct.toFixed(1)}%</div>
          <div class="cc-hero-s">${esc2(c0 ? c0.cause : '—')}异常贡献最高</div>
        </div>
        <div class="cc-spark-wrap">${spk.html}
          <div class="cc-bubble" style="left:${spk.left.toFixed(2)}%;bottom:${spk.bottom.toFixed(2)}%">
            第${spk.mi + 1}周 <b>${up ? '+' : ''}${d.pct.toFixed(1)}%</b></div>
        </div>
      </div>`;
    }
    const deltaTxt = series ? (() => {
      const d = deltaFor(series); return (d.pct >= 0 ? '+' : '') + d.pct.toFixed(1) + '%';
    })() : '—';

    /* 三卡等宽：异常摘要 / 关键原因排行（青绿圆点）/ 类目横向对比（单紫条） */
    const cards = `
      <div class="cc-cards">
        <div class="cc-card">
          <h4>${CC_ICO}异常摘要</h4>
          <div class="cc-big ${/^-/.test(deltaTxt) ? 'good' : ''}">${deltaTxt.includes('—') ? '—' : deltaTxt}</div>
          <div class="cc-kvs">
            <div class="cc-kv"><span>最新周订单</span><b>${(lastT.orders || 0).toLocaleString()}</b></div>
            <div class="cc-kv"><span>差评条数</span><b>${cat || neg == null ? '—' : Number(neg).toLocaleString()}</b></div>
            <div class="cc-kv"><span>异常周</span><b>${dd && dd.anomalies ? dd.anomalies.length : '—'}</b></div>
          </div>
        </div>
        <div class="cc-card"><h4>${CC_ICO}关键原因排行</h4>
          ${causes.length ? causes.map((c) => `
          <div class="ccr-row">
            <span class="ccr-dot"></span>
            <div class="ccr-mid"><span class="ccr-name">${esc2(c.cause)}</span>
              <span class="ccr-bar"><i style="width:${(c.share / causeMax * 100).toFixed(1)}%"></i></span></div>
            <span class="ccr-v">${Number(c.share).toFixed(1)}%</span>
          </div>`).join('') : '<div class="cc-empty">暂无数据</div>'}
        </div>
        <div class="cc-card"><h4>${CC_ICO}类目横向对比</h4>
          ${cats4.length ? cats4.map((c) => `
          <div class="ccb-row">
            <span class="ccb-l">${esc2(c.category)}</span>
            <div class="ccb-t"><i class="ccb-cur" style="width:${(c.return_rate / maxRr * 100).toFixed(1)}%"></i></div>
            <span class="ccb-v"><b>${Number(c.return_rate).toFixed(2)}%</b></span>
          </div>`).join('') : '<div class="cc-empty">暂无数据</div>'}
        </div>
      </div>`;

    /* 筛选条：时间三胶囊 · 类目（cat 状态驱动服务端联动） · 指标单选 */
    const catsRaw = (dd && dd.by_category || []).map((c) => c.category);
    const bar = `
      <div class="cc-bar">` +
        ['本周', '近4周', '近12周'].map((x) =>
          `<span class="ccf ccf-t${x === span ? ' on' : ''}" data-span="${x}">${x}</span>`).join('') +
        `<i class="ccf-sep"></i>` +
        ['全店', ...catsRaw].map((c) =>
          `<span class="ccf ccf-c${(c === '全店' ? cat === null : cat === c) ? ' on' : ''}">${esc2(c)}</span>`).join('') +
        `<i class="ccf-sep"></i>` +
        ['退货率', '差评率'].map((m) =>
          `<span class="ccf ccf-m${m === metric ? ' on' : ''}" data-m="${m}">
             <i class="ccf-dot"></i>${m}</span>`).join('') + `
      </div>`;

    /* 导航由 patch() 持久插在大标题之前，这里只渲染筛选条及以下 */
    wrap.innerHTML = bar + hero + cards;

    /* 同步底部通宽输入框上方的两个定位气泡文案 */
    const topCause = c0 ? c0.cause : '—';
    document.querySelectorAll('.composer-wrap .cc-hints span')
      .forEach((s) => { s.textContent = '已为你定位 Top 1 异常原因：' + topCause; });

    /* 交互绑定：时间三胶囊（切换重算）/ 类目单选（纯 UI）/ 指标单选（重算） */
    wrap.querySelectorAll('.ccf-t').forEach((ch) => {
      ch.addEventListener('click', () => {
        if (span === ch.dataset.span) return;
        span = ch.dataset.span;
        render();
      });
    });
    wrap.querySelectorAll('.ccf-c').forEach((ch) => {
      ch.addEventListener('click', () => {
        const name = ch.textContent.trim();
        const next = name === '全店' ? null : name;
        if (next === cat) return;
        cat = next;          /* 服务端整页重算：tail/causes/anomalies/orders 随类目变 */
        render();
      });
    });
    wrap.querySelectorAll('.ccf-m').forEach((ch) => {
      ch.addEventListener('click', () => {
        if (metric === ch.dataset.m) return;
        metric = ch.dataset.m;
        render();
      });
    });

    countUp(wrap);
    bindTilt(wrap);
  }

  /* ================================================================ 态势全景 · 左侧「范围」面板
     可视化替换顶部 .bi-scope 横条：点击面板项 → 程序化点击原 .bi-scope-i
     （React onChange）→ 整页真实联动，前端不算数。
     自定义类目只存 localStorage；后端类目表里没有的项不可选中（如实提示），
     因为后端对未知类目会静默退回全店 —— 绝不能把全店数据冒充成新范围。 */
  function enhanceScopePanel() {
    const bi = document.querySelector('.bi-scope');
    if (!bi) {
      const stale = document.querySelector('.glass-scope2');
      if (stale) stale.remove();
      return;
    }
    bi.style.display = 'none';
    const pg = document.querySelector('.pg-kpi-row')?.parentElement || document.querySelector('.pg');
    if (!pg) return;
    let panel = pg.querySelector(':scope > .glass-scope2');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'glass-scope2';
      pg.appendChild(panel);          /* 追加到网格末尾，不扰动既有 nth-child 索引 */
    }
    if (panel.dataset.built === '1') { syncScopePanel(panel, bi); return; }
    panel.dataset.built = '1';
    panel.innerHTML =
      '<button type="button" class="gs2-fold" title="收起/展开">‹</button>' +
      '<div class="cc-range-head">范围</div>' +
      '<div class="cc-range gs2-list"><span class="cc-range-bg"></span></div>' +
      '<div class="gs2-add"><button type="button" class="gs2-add-b">＋ 添加类目</button>' +
      '<div class="gs2-add-form"><input type="text" maxlength="8" placeholder="名称，回车确认">' +
      '</div></div>' +
      '<div class="gs2-note"></div>' +
      '<div class="cc-range-foot gs2-foot">口径：全店 · 52 周</div>';
    const input = panel.querySelector('input');
    const addB = panel.querySelector('.gs2-add-b');
    panel.querySelector('.gs2-fold').addEventListener('click', () => {
      panel.classList.toggle('folded');
      if (!panel.classList.contains('folded')) syncScopePanel(panel, bi);
    });
    const closeAdd = () => { panel.classList.remove('adding'); delete input.dataset.edit; };
    addB.addEventListener('click', () => {
      panel.classList.add('adding');
      input.value = '';
      delete input.dataset.edit;
      setTimeout(() => input.focus(), 60);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closeAdd(); return; }
      if (e.key !== 'Enter') return;
      const name = input.value.trim();
      if (!name) { closeAdd(); return; }
      const names = [...panel.querySelectorAll('.cc-pill')].map((p) => p.dataset.name);
      const editing = input.dataset.edit;
      if (names.includes(name) && name !== editing) { gs2Note(panel, '已存在同名类目'); return; }
      const arr = readCustoms().filter((c) => c !== editing && c !== name);
      arr.push(name);
      writeCustoms(arr);
      closeAdd();
      buildScopeList(panel, bi, name);
    });
    buildScopeList(panel, bi);
    syncScopePanel(panel, bi);
  }

  const SCOPE_LS = 'glass-scope2-custom-v1';
  const readCustoms = () => {
    try { return JSON.parse(localStorage.getItem(SCOPE_LS) || '[]') || []; } catch { return []; }
  };
  const writeCustoms = (a) => { try { localStorage.setItem(SCOPE_LS, JSON.stringify(a)); } catch {} };
  const escH = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function scopeOnName(bi) {
    const on = bi.querySelector('.bi-scope-i.on');
    return on ? on.textContent.trim() : '全店';
  }

  function gs2Note(panel, msg) {
    const n = panel.querySelector('.gs2-note');
    if (!n) return;
    n.textContent = msg;
    n.classList.add('show');
    clearTimeout(n._t);
    n._t = setTimeout(() => n.classList.remove('show'), 2400);
  }

  function buildScopeList(panel, bi, flashName) {
    const list = panel.querySelector('.gs2-list');
    if (!list) return;
    const real = [...bi.querySelectorAll('.bi-scope-i')].map((b) => b.textContent.trim());
    const names = real.length ? real : ['全店'];
    const customs = readCustoms().filter((c) => !names.includes(c));
    const onName = scopeOnName(bi);
    list.innerHTML = '<span class="cc-range-bg"></span>' +
      [...names, ...customs].map((n) => {
        const isCustom = customs.includes(n);
        return '<div class="gs2-it' + (isCustom ? ' gs2-custom' : '') +
          (n === flashName ? ' gs2-flash' : '') + '" data-name="' + escH(n) + '">' +
          '<button type="button" class="cc-pill' + (n === onName ? ' on' : '') +
          '" data-name="' + escH(n) + '" data-custom="' + (isCustom ? 1 : 0) + '">' + escH(n) + '</button>' +
          (isCustom ? '<span class="gs2-x" title="删除">×</span>' : '') +
          '</div>';
      }).join('');
    list.querySelectorAll('.gs2-it').forEach((it) => {
      const pill = it.querySelector('.cc-pill');
      pill.addEventListener('click', () => {
        if (pill.dataset.custom === '1') {
          gs2Note(panel, '「' + it.dataset.name + '」暂无数据源，仅作自定义标记');
          return;
        }
        const target = [...bi.querySelectorAll('.bi-scope-i')]
          .find((b) => b.textContent.trim() === it.dataset.name);
        if (!target || target.classList.contains('on')) return;
        moveBg(panel, pill, true);           /* 本地先动画，React 重渲染后 sync 跳过 */
        target.click();                      /* React onChange → 整页服务端联动 */
      });
      pill.addEventListener('dblclick', () => {
        if (pill.dataset.custom !== '1') return;
        const input = panel.querySelector('input');
        panel.classList.add('adding');
        input.value = it.dataset.name;
        input.dataset.edit = it.dataset.name;
        setTimeout(() => input.focus(), 60);
      });
      const x = it.querySelector('.gs2-x');
      if (x) x.addEventListener('click', () => {
        it.classList.add('gs2-out');         /* 平滑淡出后再删 */
        setTimeout(() => {
          writeCustoms(readCustoms().filter((c) => c !== it.dataset.name));
          buildScopeList(panel, bi);
        }, 280);
      });
    });
    requestAnimationFrame(() => {
      const on = list.querySelector('.cc-pill.on');
      if (on && panel.dataset.ccAnim !== '1') moveBg(panel, on, false);
    });
  }

  function syncScopePanel(panel, bi) {
    const onName = scopeOnName(bi);
    panel.querySelectorAll('.cc-pill').forEach((p) =>
      p.classList.toggle('on', p.dataset.name === onName));
    const foot = panel.querySelector('.gs2-foot');
    if (foot) foot.innerHTML = '口径：<b>' + escH(onName) + '</b> · 52 周';
    const on = panel.querySelector('.cc-pill.on');
    if (on && panel.dataset.ccAnim !== '1') moveBg(panel, on, false);
  }

  /* 液态高亮胶囊：滑向新位置，移动中沿方向拉长，到位弹性回弹 */
  function moveBg(fp, target, animate) {
    const bg = fp.querySelector('.cc-range-bg');
    if (!target || !bg) return;
    const host = target.parentElement;
    const y = (host && host !== fp ? host.offsetTop : 0) + target.offsetTop;
    const h = target.offsetHeight;
    bg.style.height = h + 'px';
    if (!animate) {
      bg.style.transition = 'none';
      bg.style.transform = 'translateY(' + y + 'px)';
      return;
    }
    fp.dataset.ccAnim = '1';
    bg.style.transition = 'transform .28s cubic-bezier(.55,.06,.68,.19)';
    bg.style.transform = 'translateY(' + y + 'px) scaleY(1.22)';
    setTimeout(() => {
      bg.style.transition = 'transform .42s cubic-bezier(.26,1.65,.5,1)';
      bg.style.transform = 'translateY(' + y + 'px) scaleY(1)';
      setTimeout(() => { delete fp.dataset.ccAnim; }, 450);
    }, 300);
  }

  /* 磨砂卡片：光斑跟随光标 + 边框局部提亮 + 轻微 3D 倾斜，移出平滑复原 */
  function bindTilt(scope) {
    scope.querySelectorAll('.cc-hero, .cc-card, .cc-filterpanel').forEach((card) => {
      if (card.dataset.ccTilt) return;
      card.dataset.ccTilt = '1';
      card.addEventListener('pointermove', (e) => {
        const r = card.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
        card.style.setProperty('--mx', (px * 100).toFixed(2) + '%');
        card.style.setProperty('--my', (py * 100).toFixed(2) + '%');
        card.style.transform = 'perspective(700px) rotateX(' + ((py - .5) * -4.5).toFixed(2) +
          'deg) rotateY(' + ((px - .5) * 4.5).toFixed(2) + 'deg)';
      });
      card.addEventListener('pointerleave', () => { card.style.transform = ''; });
    });
  }

  function aiBadge(show) {
    if (!ai) {
      ai = document.createElement('div');
      ai.className = 'cc-ai';
      ai.title = 'AI 诊断助手';
      ai.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2L12 3z"/></svg>`;
      document.body.appendChild(ai);
    }
    ai.style.display = show ? 'grid' : 'none';
  }

  let tmr = null;
  function patch() {
    const onChat = !!document.querySelector('.app.app-chat');
    aiBadge(onChat);
    enhanceScopePanel();   /* 态势全景左侧范围面板（问答页无 .bi-scope 时自动空转） */
    if (!onChat) return;
    /* 输入框提示文案对齐设计稿（纯文案，不动数据逻辑） */
    const ta = document.querySelector('.composer textarea, .composer-wrap textarea');
    if (ta && ta.getAttribute('placeholder') !== '告诉我本周差评变化的核心原因')
      ta.setAttribute('placeholder', '告诉我本周差评变化的核心原因');
    /* 输入框下方工具图标行：刷新 / 导出 / 设置 / 全屏 */
    const comp = document.querySelector('.composer-wrap') ||
      document.querySelector('.composer')?.parentElement;
    if (comp && !comp.querySelector('.cc-tools')) {
      const tools = document.createElement('div');
      tools.className = 'cc-tools';
      tools.innerHTML = `
        <button type="button" data-act="reload" title="刷新">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 3v4h-4"/></svg>刷新</button>
        <button type="button" data-act="sort" title="排序">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 7h10M4 12h7M4 17h4M17 5v14M17 19l-3-3M17 19l3-3"/></svg>排序</button>
        <button type="button" data-act="export" title="导出">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V4M8 8l4-4 4 4M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>导出</button>
        <button type="button" data-act="full" title="全屏">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>全屏</button>`;
      tools.addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.act === 'reload') location.reload();
        if (b.dataset.act === 'full') {
          if (document.fullscreenElement) document.exitFullscreen();
          else document.documentElement.requestFullscreen?.();
        }
      });
      const anchor = comp.querySelector('.composer') || comp.firstElementChild;
      anchor.insertAdjacentElement('afterend', tools);
    }
    const empty = document.querySelector('.stage-scroll .empty');
    if (!empty) return;
    if (!empty.classList.contains('cc-on')) {
      empty.classList.add('cc-on');
      /* 顶部导航持久插在大标题之前（首页房子图标 + 诊断中心/原因分析选中下划线） */
      if (!empty.querySelector(':scope > .cc-topnav')) {
        const nav = document.createElement('div');
        nav.className = 'cc-topnav';
        nav.innerHTML = `
          <span class="cc-nav-home" title="首页"><svg viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 11.5 12 4l9 7.5M5.5 10v9h13v-9"/></svg></span>
          <a href="#/dashboard">首页</a>
          <a href="#/attribution" class="on">诊断中心</a>
          <a href="#/reasons" class="on">原因分析</a>
          <a href="#/plan">动作建议</a>`;
        empty.insertBefore(nav, empty.firstElementChild);
      }
      const t = empty.querySelector('.empty-title');
      if (t && t.dataset.ccT !== '1') {
        t.dataset.ccT = '1';
        t.textContent = '本周差评，因何而涨？';
        if (!empty.querySelector('.cc-sub')) {
          const s = document.createElement('div');
          s.className = 'cc-sub';
          s.textContent = '异常识别 · 原因定位 · 动作建议';
          t.insertAdjacentElement('afterend', s);
        }
      }
      wrap = document.createElement('div');
      wrap.className = 'cc-wrap';
      empty.appendChild(wrap);
      wrap.innerHTML = '<div class="cc-loading">正在加载诊断数据…</div>';
      ensureData().then(render);
    }
    /* landing：通宽输入框正上方左右各一个定位气泡（数据到达后 render 回填文案） */
    if (comp && !comp.querySelector('.cc-hints')) {
      const hints = document.createElement('div');
      hints.className = 'cc-hints';
      hints.innerHTML =
        '<span>正在定位 Top 1 异常原因…</span><span>正在定位 Top 1 异常原因…</span>';
      const cm = comp.querySelector('.composer');
      if (cm) comp.insertBefore(hints, cm);
    }
    /* 右栏「筛选详情」面板（问一问保持原样）：大白卡内嵌四张小卡；
       「范围」垂直胶囊面板移到态势全景页（见 enhanceScopePanel） */
    const rr = document.querySelector('.chat-rr .rr-scroll');
    if (rr && !rr.querySelector('.cc-filterpanel')) {
      const fpGroups = [
        { name: '原因筛选', tags: ['描述与实物不符', '尺码版型'] },
        { name: '类目筛选', tags: ['全店', '服饰'] },
        { name: '时间对比', tags: [] },
        { name: '建议动作', tags: ['包装升级', '描述校准', '预警管理', '发货校验', '主动补偿'], act: true },
      ];
      const div = document.createElement('div');
      div.className = 'rr-sec cc-filterpanel';
      div.innerHTML = `
        <div class="rr-sec-h">筛选详情</div>
        ${fpGroups.map((g) => `
          <div class="cc-fp-g open${g.act ? ' cc-fp-g-act' : ''}">
            <div class="cc-fp-h">${g.act ? `
                <span class="cc-fp-act"><i class="ccf-radio"></i>${g.name}</span>
                <div class="cc-tg on"><span class="cc-tg-dot"></span></div>` : `
                <div class="cc-tg on"><span class="cc-tg-dot"></span></div>
                <span class="cc-fp-t">${g.name}</span>`}
            </div>${g.tags.length ? `
            <div class="cc-fp-b"><div class="ccf-tags">${g.tags
              .map((c) => `<span class="${g.act && c === '描述校准' ? 'on' : ''}">${c}</span>`).join('')}</div></div>` : ''}
          </div>`).join('')}`;
      div.querySelectorAll('.cc-fp-g').forEach((g) => {
        g.querySelector('.cc-fp-h').addEventListener('click', (e) => {
          if (e.target.closest('.cc-tg')) return;
          g.classList.toggle('open');
        });
        g.querySelector('.cc-tg').addEventListener('click', (e) => {
          e.stopPropagation();
          g.querySelector('.cc-tg').classList.toggle('on');
        });
      });
      rr.insertBefore(div, rr.firstChild);
    }
  }
  new MutationObserver(() => { clearTimeout(tmr); tmr = setTimeout(patch, 200); })
    .observe(document.body, { childList: true, subtree: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', patch);
  else patch();
})();