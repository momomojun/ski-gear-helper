"use strict";
/* 动效：滚动出现、数字滚动、山顶飘雪、仪表盘/折线/条形图的入场动画。
 * 系统开了“减少动态效果”（prefers-reduced-motion）时全部关闭，直接显示最终状态。 */
const Motion = (() => {
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reduced = () => mq.matches;

  // 滚动出现：元素带 .rv，进入视口时加 .in；同一批进入的按顺序错开一点
  let io = null;
  function reveal(root = document) {
    const els = [...root.querySelectorAll(".rv:not(.in)")];
    if (!els.length) return;
    if (reduced() || !("IntersectionObserver" in window)) { els.forEach((e) => e.classList.add("in")); return; }
    if (!io) {
      io = new IntersectionObserver((entries) => {
        let k = 0;
        for (const en of entries) {
          if (!en.isIntersecting) continue;
          en.target.style.transitionDelay = Math.min(k++ * 45, 360) + "ms";
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      }, { rootMargin: "0px 0px -4% 0px", threshold: 0.01 });
    }
    els.forEach((e) => io.observe(e));
  }

  // 数字滚动：从 0 滚到目标值
  function countUp(el, to, fmt = (v) => Math.round(v).toLocaleString(), ms = 900) {
    if (!el) return;
    if (reduced() || !isFinite(to) || to === 0) { el.textContent = fmt(to); return; }
    const t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / ms);
      el.textContent = fmt(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // 飘雪：低密度、约 35 帧/秒；滚出视口或切到别的标签页时暂停
  function snow(canvas) {
    if (!canvas || reduced()) return () => {};
    const ctx = canvas.getContext("2d");
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let w = 0, h = 0, flakes = [], raf = 0, visible = true, last = 0, col = "#fff";
    const mk = (anywhere) => ({ x: Math.random() * w, y: anywhere ? Math.random() * h : -6, r: 0.6 + Math.random() * 1.8,
      vy: 0.16 + Math.random() * 0.42, sway: Math.random() * 6.28, a: 0.3 + Math.random() * 0.55 });
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      w = r.width; h = r.height;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      flakes = Array.from({ length: Math.round(Math.min(80, (w * h) / 10000)) }, () => mk(true));
      col = getComputedStyle(canvas).color || "#fff";
    };
    const tick = (t) => {
      raf = requestAnimationFrame(tick);
      if (!visible || document.hidden || t - last < 28) return;
      const dt = Math.min(3, (t - last) / 16.7 || 1);
      last = t;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = col;
      for (const f of flakes) {
        f.y += f.vy * dt; f.sway += 0.012 * dt; f.x += Math.sin(f.sway) * 0.22 * dt;
        if (f.y > h + 4) Object.assign(f, mk(false));
        ctx.globalAlpha = f.a;
        ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, 6.2832); ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
    resize();
    const ro = new ResizeObserver(resize); ro.observe(canvas);
    const vis = new IntersectionObserver((es) => { visible = es[0].isIntersecting; }); vis.observe(canvas);
    const mo = new MutationObserver(() => { col = getComputedStyle(canvas).color || "#fff"; });  // 切换深色模式
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); vis.disconnect(); mo.disconnect(); };
  }

  // 入场：仪表盘圆环、折线、条形图（CSS 里 .on 状态有过渡）
  function enter(root) {
    if (!root) return;
    const els = root.querySelectorAll(".gauge, .chart-wrap, .bars");
    if (reduced()) { els.forEach((e) => e.classList.add("on", "instant")); return; }
    requestAnimationFrame(() => requestAnimationFrame(() => els.forEach((e) => e.classList.add("on"))));
  }

  return { reduced, reveal, countUp, snow, enter };
})();
