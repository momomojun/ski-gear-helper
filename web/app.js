"use strict";
/* 滑雪装备小帮手（SkiDeals）—— 前端（原生 JS，无需构建）
 * 数据流：/api/categories 拿到分类 → /api/catalog?cat=xxx 一次拿到该分类全部型号 → 浏览器里即时筛选/排序；
 *         点开卡片再请求详情。 */

// ================================================================ 常量
const GENDER = { men: "男款", women: "女款", unisex: "中性", kids: "儿童" };
const TYPE = { all_mountain: "全山", frontside: "道内/刻滑", race: "竞技", freeride: "粉雪/野雪",
  park: "公园/自由式", touring: "登山/野外", none: "未分类" };
const COND = { new: "全新", demo: "试滑板", used: "二手", blem: "瑕疵/开箱" };
const TIER = { A: "大型零售商", B: "专业雪具店", brand: "品牌官网", caution: "⚠ 谨慎" };
const COLORS = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];
const BASE_F = { minOff: 0, priceMin: "", priceMax: "", brands: [], years: [], conds: ["new"], retailers: [],
  inStock: true, sort: "off", feats: [], sizes: [] };
const CAT_F = {  // 各分类特有的筛选项
  ski: { lenMin: "", lenMax: "", bind: "flat", types: [], waistMin: "", waistMax: "" },
  pole: { lenMin: "", lenMax: "" },
  boot: { mondo: "", flexSel: [], lastSel: [] },
  binding: { brakeFor: "", dinSel: [] },
};
const DIN_BANDS = [["≤10", 0, 10], ["11–13", 11, 13], ["14–16", 14, 16], ["≥17", 17, 30]];
const FLEX_BANDS = [["≤80（入门）", 0, 80], ["85–100（进阶）", 85, 100], ["105–120（高级）", 105, 120], ["≥130（专家）", 125, 200]];
const LAST_BANDS = [["窄楦 ≤98mm", "narrow"], ["中楦 99–101mm", "medium"], ["宽楦 ≥102mm", "wide"]];
const SIZED = ["ski", "pole", "boot", "binding"];  // 这些分类有专门的尺码筛选
const PAGE = 60;

// ================================================================ 工具
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (v === "" || v == null || isNaN(+v) ? null : +v);
const enc = encodeURIComponent;
function usd(v) {
  if (v == null) return "—";
  return "$" + (v >= 1000 ? Math.round(v).toLocaleString() : (+v).toFixed(v % 1 ? 2 : 0));
}
const yen = (v) => (v == null ? "—" : Math.round(v).toLocaleString() + " 円");
const rmb = (v) => (v == null ? "—" : "¥" + Math.round(v).toLocaleString());
function money(v, cur) { return cur === "JPY" ? yen(v) : cur === "CNY" ? rmb(v) : usd(v); }
function ago(iso) {
  if (!iso) return "从未";
  const m = (Date.now() - new Date(iso).getTime()) / 60000;
  if (m < 1) return "刚刚";
  if (m < 60) return Math.round(m) + " 分钟前";
  if (m < 60 * 24) return Math.round(m / 60) + " 小时前";
  return Math.round(m / 1440) + " 天前";
}
function thumb(url) {
  if (!url) return url;
  return url.includes("cdn.shopify.com") ? url + (url.includes("?") ? "&" : "?") + "width=420" : url;
}
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "Content-Type": "application/json", "X-SkiDeals": "1" } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const loadJSON = (k, d) => { try { return JSON.parse(localStorage.getItem(k) || "null") ?? d; } catch (e) { return d; } };

// ================================================================ 状态
const S = {
  cats: [], catInfo: {}, feat: {}, sizeOrder: [], catId: localStorage.getItem("sd.cat") || "ski",
  data: {}, cat: null, meta: null, q: "", shown: PAGE, route: "deals", detail: null,
  g: loadJSON("sd.g", { gender: "all", incUnisex: true }),  // 性别选择在各分类之间共用
  f: null,
};
function defaultF(cat) { return { ...BASE_F, ...(CAT_F[cat] || {}), feats: [], sizes: [] }; }
function loadF(cat) { return { ...defaultF(cat), ...loadJSON("sd.f." + cat, {}) }; }
function saveF() {
  localStorage.setItem("sd.f." + S.catId, JSON.stringify(S.f));
  localStorage.setItem("sd.g", JSON.stringify(S.g));
}
const retName = (id) => S.cat?.retailers[id]?.name || id;
const catName = (id) => (S.catInfo[id]?.name || id).split("（")[0];
const season = () => S.meta?.season || new Date().getFullYear() + (new Date().getMonth() >= 6 ? 1 : 0);
function yearBucket(y) { if (!y) return "na"; return y >= season() - 2 ? String(y) : "old"; }
const YEAR_LABEL = (b) => (b === "na" ? "未标注年份" : b === "old" ? "更早" : b + " 款");
const featLabel = (k) => S.feat[k] || k;
const brandLabel = (b) => (b ? esc(b) : '<span class="muted">未标品牌</span>');  // 网站没写品牌、也认不出来的商品

// ================================================================ 筛选逻辑
function sizeMatch(cat, f, z) {  // z = [尺码, 是否有货, 价格]
  const n = z[0];
  if (cat === "ski" || cat === "pole") {
    const lo = num(f.lenMin), hi = num(f.lenMax);
    return (!lo || n >= lo) && (!hi || n <= hi);
  }
  if (cat === "boot") {  // 同一个鞋壳：26.0 和 26.5 是同一个鞋壳
    const t = num(f.mondo), v = parseFloat(n);
    return !t || (!isNaN(v) && Math.floor(v) === Math.floor(t));
  }
  if (cat === "binding") {
    const w = num(f.brakeFor), v = parseInt(n, 10);
    return !w || (!isNaN(v) && v >= w && v <= w + 20);
  }
  const sel = f.sizes || [];
  return !sel.length || String(n).toUpperCase().split("/").some((x) => sel.includes(x));
}
function sizeFilterActive(cat, f) {
  if (cat === "ski" || cat === "pole") return !!(num(f.lenMin) || num(f.lenMax));
  if (cat === "boot") return !!num(f.mondo);
  if (cat === "binding") return !!num(f.brakeFor);
  return (f.sizes || []).length > 0;
}
function offerPass(o, f, skip, cat) {
  if (f.conds.length && !f.conds.includes(o.cd)) return null;
  if (skip !== "retailers" && f.retailers.length && !f.retailers.includes(o.r)) return null;
  if (skip !== "years" && f.years.length && !f.years.includes(yearBucket(o.y))) return null;
  if (f.inStock && !o.a) return null;
  if (skip !== "sizes" && sizeFilterActive(cat, f)) {  // 只看“我的尺码”有货的报价，价格按这个尺码算
    const zs = o.z.filter((z) => sizeMatch(cat, f, z) && (z[1] || !f.inStock));
    if (!zs.length) return null;
    return { o, p: Math.min(...zs.map((z) => z[2] ?? o.p)), fit: zs.map((z) => z[0]) };
  }
  return { o, p: o.p };
}
function bandHit(bands, labels, v) {
  return labels.some((lab) => { const b = bands.find((x) => x[0] === lab); return b && v >= b[1] && v <= b[2]; });
}
function lastClass(l) {
  if (typeof l === "number") return l <= 98 ? "narrow" : l <= 101 ? "medium" : "wide";
  const s = String(l || "");
  return s.includes("LV") ? "narrow" : s.includes("MV") ? "medium" : s.includes("HV") ? "wide" : null;
}
function famPass(fam, f, skip) {
  const cat = S.catId;
  if (S.q) {
    const hay = (fam.b + " " + fam.m).toLowerCase();
    if (!S.q.split(/\s+/).every((w) => hay.includes(w))) return null;
  }
  const g = S.g;
  if (g.gender !== "all") {
    const ok = fam.g === g.gender || (g.incUnisex && fam.g === "unisex" && (g.gender === "men" || g.gender === "women"));
    if (!ok) return null;
  }
  if (skip !== "brands" && f.brands.length && !f.brands.includes(fam.b)) return null;
  if (skip !== "feats" && f.feats.length && !f.feats.every((k) => (fam.ft || []).includes(k))) return null;
  const sp = fam.sp || {};
  if (cat === "ski") {
    if (skip !== "types" && f.types.length && !f.types.includes(fam.t || "none")) return null;
    if (f.bind === "flat" && fam.bd) return null;
    if (f.bind === "with" && !fam.bd) return null;
    const wlo = num(f.waistMin), whi = num(f.waistMax);
    if ((wlo || whi) && (!fam.w || (wlo && fam.w < wlo) || (whi && fam.w > whi))) return null;
  } else if (cat === "binding") {
    if (f.dinSel.length && !(sp.din_max && bandHit(DIN_BANDS, f.dinSel, sp.din_max))) return null;
  } else if (cat === "boot") {
    if (f.flexSel.length && !(sp.flex && bandHit(FLEX_BANDS, f.flexSel, sp.flex))) return null;
    if (f.lastSel.length && !f.lastSel.includes(lastClass(sp.last))) return null;
  }
  const offers = [];
  for (const o of fam.o) { const x = offerPass(o, f, skip, cat); if (x) offers.push(x); }
  if (!offers.length) return null;
  let best = offers[0];
  for (const x of offers) if (x.p < best.p) best = x;
  const msrp = fam.msrp[String(best.o.y)] ?? fam.msrp.None ?? best.o.c;
  const off = msrp && msrp > best.p ? Math.round((1 - best.p / msrp) * 100) : 0;
  const plo = num(f.priceMin), phi = num(f.priceMax);
  if ((plo && best.p < plo) || (phi && best.p > phi) || (f.minOff && off < f.minOff)) return null;
  const stores = new Set(offers.map((x) => x.o.r)).size;
  const maxP = Math.max(...offers.map((x) => x.p));
  return { fam, best, off, msrp, stores, maxP, offers };
}
const SORTS = {
  off: ["折扣最大", (a, b) => b.off - a.off || a.best.p - b.best.p],
  save: ["省得最多($)", (a, b) => ((b.msrp || 0) - b.best.p) - ((a.msrp || 0) - a.best.p)],
  price: ["价格从低到高", (a, b) => a.best.p - b.best.p],
  priceDesc: ["价格从高到低", (a, b) => b.best.p - a.best.p],
  stores: ["比价网站最多", (a, b) => b.stores - a.stores || b.off - a.off],
  spread: ["各家价差最大", (a, b) => b.maxP / b.best.p - a.maxP / a.best.p],
  name: ["品牌 A→Z", (a, b) => (a.fam.b + a.fam.m).localeCompare(b.fam.b + b.fam.m)],
};
function results() {
  const out = [];
  for (const fam of S.cat.families) { const r = famPass(fam, S.f, null); if (r) out.push(r); }
  out.sort((SORTS[S.f.sort] || SORTS.off)[1]);
  return out;
}
function facets() {
  const count = (skip, keys) => {
    const m = {};
    for (const fam of S.cat.families) {
      const r = famPass(fam, S.f, skip);
      if (r) for (const k of keys(r)) m[k] = (m[k] || 0) + 1;
    }
    return m;
  };
  const out = {
    brands: count("brands", (r) => [r.fam.b]),
    retailers: count("retailers", (r) => [...new Set(r.offers.map((x) => x.o.r))]),
    years: count("years", (r) => [...new Set(r.offers.map((x) => yearBucket(x.o.y)))]),
    feats: count("feats", (r) => r.fam.ft || []),
  };
  if (S.catId === "ski") out.types = count("types", (r) => [r.fam.t || "none"]);
  if (!SIZED.includes(S.catId)) {
    out.sizes = count("sizes", (r) => [...new Set(r.offers.flatMap((x) => x.o.z.filter((z) => z[1] || !S.f.inStock)
      .flatMap((z) => String(z[0]).toUpperCase().split("/"))))]);
  }
  return out;
}

// ================================================================ 分类栏 & 找折扣页
function catBar() {
  const groups = {};
  for (const c of S.cats) {
    if (!c.fams && c.id !== S.catId) continue;
    (groups[c.group] = groups[c.group] || []).push(c);
  }
  return `<div class="catbar">${Object.entries(groups).map(([g, cs]) => `<div class="catgroup"><span class="catg">${esc(g)}</span>${cs.map((c) =>
    `<a href="#/c/${c.id}" class="catchip ${c.id === S.catId ? "on" : ""}">${esc(catName(c.id))}<span>${c.fams.toLocaleString()}</span></a>`).join("")}</div>`).join("")}</div>`;
}
function seasonTip() {
  const m = new Date().getMonth() + 1, s = season();
  if (m >= 9 && m <= 10) return `<b>现在是旧款清仓季（9–10 月）。</b>新一季 ${s} 款陆续到货，上一季 ${s - 1} 款常见 30–50% off。服装、雪镜、头盔同样有换季折扣；想买新款可以等 11 月底黑五。`;
  if (m === 11) return "<b>11 月底是黑五 / 网一。</b>新款也常有 15–25% 折扣，旧款会进一步降价——先把想要的型号加入关注。";
  if (m === 12 || m <= 2) return "<b>现在是雪季旺季（12–2 月）。</b>折扣较少、断码多；不急的话把型号加入关注，等降价提醒。";
  if (m <= 4) return "<b>3–4 月季末清仓，全年折扣最大（30–60%）。</b>缺点是热门尺码容易断码，建议用“我的尺码”筛选。";
  return "<b>5–8 月是淡季。</b>只有零星清货；日本店铺这时会推出下一季“早期予約”（预订）折扣，可以在详情页对比日本价格。";
}
function sizeFilterHTML(f) {
  const cat = S.catId;
  if (cat === "ski" || cat === "pole") {
    return `<div class="fgroup"><h4>我的长度 (cm) ${cat === "ski" ? '<button class="link-btn small" data-act="open-sizer">尺码助手</button>' : ""}</h4>
      <div class="range"><input id="lenMin" inputmode="numeric" placeholder="最短" value="${esc(f.lenMin)}">–<input id="lenMax" inputmode="numeric" placeholder="最长" value="${esc(f.lenMax)}"></div>
      <div class="note">只显示这个长度有货的报价，价格按该长度计算。${cat === "pole" ? "雪杖长度 ≈ 身高 × 0.7。" : ""}</div></div>`;
  }
  if (cat === "boot") {
    return `<div class="fgroup"><h4>我的鞋码 (Mondo) <span class="hint">≈ 脚长 cm</span></h4>
      <div class="range"><input id="mondo" inputmode="decimal" placeholder="如 26.5" value="${esc(f.mondo)}"></div>
      <div class="note">26.0 和 26.5 是同一个鞋壳，会一起显示；只显示这个码有货的报价。</div></div>`;
  }
  if (cat === "binding") {
    return `<div class="fgroup"><h4>我的雪板腰宽 (mm)</h4>
      <div class="range"><input id="brakeFor" inputmode="numeric" placeholder="如 94" value="${esc(f.brakeFor)}"></div>
      <div class="note">只显示刹车宽度在 腰宽 ~ 腰宽+20mm 且有货的报价（刹车比雪板窄就装不上）。</div></div>`;
  }
  return `<div class="fgroup"><h4>尺码</h4><div class="chips" id="f-sizes"></div><div class="note">只显示这个尺码有货的报价。“S/M”这种两码合一的，选 S 或 M 都会显示。</div></div>`;
}
function dealsShell() {
  const f = S.f, g = S.g, cat = S.catId;
  const segs = [["all", "全部"], ["men", "男款"], ["women", "女款"], ["unisex", "Unisex 中性"], ["kids", "儿童"]];
  const offChips = [[0, "不限"], [20, "≥20%"], [30, "≥30%"], [40, "≥40%"], [50, "≥50%"]];
  const chipGroup = (id, list, sel, attr) => `<div class="chips" id="${id}">${list.map(([lab, val]) =>
    `<button class="chip ${sel.includes(val ?? lab) ? "on" : ""}" data-${attr}="${esc(val ?? lab)}">${esc(lab)}</button>`).join("")}</div>`;
  let special = "";
  if (cat === "ski") {
    special = `<div class="fgroup"><h4>固定器</h4>
        <div class="chips" id="chips-bind">${[["flat", "只看板身"], ["with", "含固定器套装"], ["any", "都看"]].map(([v, t]) => `<button class="chip ${f.bind === v ? "on" : ""}" data-bind="${v}">${t}</button>`).join("")}</div>
        <div class="note">板身和“板+固定器”价格没法直接比，默认只看板身；点开套装能看到“套装 vs 分开买”。</div></div>
      <div class="fgroup"><h4>类型</h4><div class="checks" id="f-types"></div></div>
      <div class="fgroup"><h4>腰宽 (mm) <span class="hint">美东冰雪建议 80–95</span></h4>
        <div class="range"><input id="waistMin" inputmode="numeric" placeholder="最窄" value="${esc(f.waistMin)}">–<input id="waistMax" inputmode="numeric" placeholder="最宽" value="${esc(f.waistMax)}"></div></div>`;
  } else if (cat === "binding") {
    special = `<div class="fgroup"><h4>最大 DIN <span class="hint">按体重/水平</span></h4>${chipGroup("chips-din", DIN_BANDS.map((b) => [b[0]]), f.dinSel, "din")}
      <div class="note">一般：体重 60kg 以下/初学选 ≤10；中级 11–13；80kg 以上或激进选 14 以上。具体 DIN 值由雪具店设定。</div></div>`;
  } else if (cat === "boot") {
    special = `<div class="fgroup"><h4>硬度 (Flex)</h4>${chipGroup("chips-flex", FLEX_BANDS.map((b) => [b[0]]), f.flexSel, "flex")}</div>
      <div class="fgroup"><h4>楦宽 (Last)</h4>${chipGroup("chips-last", LAST_BANDS.map((b) => [b[0], b[1]]), f.lastSel, "last")}
      <div class="note">亚洲人脚多数偏宽，中楦/宽楦更容易合脚；买雪鞋最好先去店里试穿或找 bootfitter。</div></div>`;
  }
  return `
  <div class="page" id="deals-page">
    ${catBar()}
    <div class="toolbar">
      <div class="seg" id="seg-g">${segs.map(([k, t]) => `<button data-g="${k}" class="${g.gender === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <label class="chk small" title="很多商品是男女通用的中性款，零售商会同时放在男款和女款里"><input type="checkbox" id="inc-unisex" ${g.incUnisex ? "checked" : ""}> 男/女款里包含中性款</label>
      <span class="spacer"></span>
      <span id="count" class="muted"></span>
      <select id="sort">${Object.entries(SORTS).map(([k, [t]]) => `<option value="${k}" ${f.sort === k ? "selected" : ""}>${t}</option>`).join("")}</select>
    </div>
    ${localStorage.getItem("sd.tip") === String(new Date().getMonth()) ? "" : `<div class="tip" id="tip"><span>💡</span><span>${seasonTip()}</span><button class="x" data-act="close-tip" title="本月不再显示">✕</button></div>`}
    <div class="layout">
      <aside class="filters" id="filters">
        ${sizeFilterHTML(f)}
        <div class="fgroup"><h4>价格 (USD)</h4>
          <div class="range"><input id="priceMin" inputmode="numeric" placeholder="$ 最低" value="${esc(f.priceMin)}">–<input id="priceMax" inputmode="numeric" placeholder="$ 最高" value="${esc(f.priceMax)}"></div></div>
        <div class="fgroup"><h4>折扣力度 <span class="hint">相对官方原价</span></h4>
          <div class="chips" id="chips-off">${offChips.map(([v, t]) => `<button class="chip ${+f.minOff === v ? "on" : ""}" data-off="${v}">${t}</button>`).join("")}</div></div>
        ${special}
        <div class="fgroup" id="fg-feats"><h4>特征</h4><div class="chips" id="f-feats"></div></div>
        <div class="fgroup"><h4>年份</h4><div class="checks" id="f-years"></div></div>
        <div class="fgroup"><h4>品牌</h4><div class="checks" id="f-brands"></div></div>
        <div class="fgroup"><h4>成色</h4><div class="checks" id="f-conds">${Object.entries(COND).map(([k, t]) => `<label class="chk"><input type="checkbox" data-cond="${k}" ${f.conds.includes(k) ? "checked" : ""}> ${t}</label>`).join("")}</div></div>
        <div class="fgroup"><h4>网站</h4><div class="checks" id="f-retailers"></div></div>
        <div class="fgroup">
          <label class="chk"><input type="checkbox" id="inStock" ${f.inStock ? "checked" : ""}> 只看有货</label>
          <div style="margin-top:10px"><button class="btn-plain btn-sm" data-act="reset">重置本分类筛选</button></div>
        </div>
      </aside>
      <div><div id="grid" class="grid"></div><div id="more"></div></div>
    </div>
  </div>`;
}
function checkList(el, items, selected, attr, fmt, limit = 999) {
  if (!el) return;
  const expanded = el.dataset.expanded === "1";
  const shown = expanded ? items : items.slice(0, limit);
  el.innerHTML = shown.map(([k, n]) => `<label class="chk"><input type="checkbox" data-${attr}="${esc(k)}" ${selected.includes(k) ? "checked" : ""}> ${fmt(k)}<span class="cnt">${n}</span></label>`).join("") +
    (items.length > limit ? `<button class="link-btn more-toggle" data-act="expand" data-target="${el.id}">${expanded ? "收起" : `显示全部 ${items.length} 个`}</button>` : "") ||
    '<span class="muted small">无</span>';
}
function sortSizes(keys) {
  const order = S.sizeOrder;
  return keys.sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    if (ia >= 0 || ib >= 0) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    const na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    return String(a).localeCompare(String(b));
  });
}
function renderFilters() {
  const fc = facets(), f = S.f;
  const sorted = (m, keep) => {
    const keys = new Set([...Object.keys(m), ...keep]);
    return [...keys].map((k) => [k, m[k] || 0]).sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
  };
  if (fc.types) checkList($("#f-types"), sorted(fc.types, f.types), f.types, "type", (k) => TYPE[k] || k);
  const yrs = sorted(fc.years, f.years).sort((a, b) => (b[0] === "na" ? -1 : a[0] === "na" ? 1 : b[0].localeCompare(a[0])));
  checkList($("#f-years"), yrs, f.years, "year", YEAR_LABEL);
  checkList($("#f-brands"), sorted(fc.brands, f.brands), f.brands, "brand", brandLabel, 12);
  checkList($("#f-retailers"), sorted(fc.retailers, f.retailers), f.retailers, "retailer",
    (k) => `${esc(retName(k))} <span class="tier ${S.cat.retailers[k]?.tier}">${TIER[S.cat.retailers[k]?.tier] || ""}</span>`, 10);
  const feats = sorted(fc.feats, f.feats);
  $("#fg-feats").classList.toggle("hidden", !feats.length);
  $("#f-feats").innerHTML = feats.map(([k, n]) => `<button class="chip ${f.feats.includes(k) ? "on" : ""}" data-feat="${esc(k)}">${esc(featLabel(k))} <span class="cnt">${n}</span></button>`).join("");
  const sz = $("#f-sizes");
  if (sz && fc.sizes) {
    const sel = f.sizes || [];
    const keys = sortSizes([...new Set([...Object.keys(fc.sizes), ...sel])]);
    // 常用字母码（S/M/L…）默认显示；数字码、童码、欧码、头围等收在“更多尺码”里
    const main = keys.filter((k) => S.sizeOrder.includes(k)), more = keys.filter((k) => !S.sizeOrder.includes(k));
    const pinned = more.some((k) => sel.includes(k));  // 选中了“更多”里的尺码时不能收起
    const open = S.moreSizes || pinned || !main.length;
    const chip = (k) => `<button class="chip ${sel.includes(k) ? "on" : ""}" data-size="${esc(k)}">${esc(k)} <span class="cnt">${fc.sizes[k] || 0}</span></button>`;
    const toggle = more.length && main.length && !pinned
      ? `<button class="link-btn more-toggle" data-act="more-sizes">${open ? "收起" : `更多尺码（数字码 / 童码 / 欧码，${more.length} 个）`}</button>` : "";
    sz.innerHTML = keys.length ? main.map(chip).join("") + (open ? more.map(chip).join("") : "") + toggle
      : '<span class="muted small">这个分类大多是均码</span>';
  }
}
function metaLine(fam, o) {
  const sp = fam.sp || {};
  const parts = [];
  if (S.catId === "ski") parts.push(TYPE[fam.t], fam.w && fam.w + "mm");
  else if (S.catId === "binding") {
    if (sp.din) parts.push(`DIN ${sp.din[0]}–${sp.din[1]}`); else if (sp.din_max) parts.push(`DIN 最大 ${sp.din_max}`);
    if (sp.brakes) parts.push(`刹车 ${sp.brakes.join("/")}mm`);
  } else if (S.catId === "boot") {
    if (sp.flex) parts.push(`Flex ${sp.flex}`);
    if (sp.last) parts.push(typeof sp.last === "number" ? `楦宽 ${sp.last}mm` : sp.last);
  } else if (S.catId === "backpack" && sp.volume) parts.push(`${sp.volume}L`);
  if (S.catId !== "ski") parts.push(...(fam.ft || []).slice(0, 2).map(featLabel));
  if (o.y) parts.push(o.y + " 款");
  return parts.filter(Boolean).join(" · ");
}
function card(r) {
  const { fam, best, off, stores, maxP } = r, o = best.o;
  const tags = [];
  if (o.lo) tags.push('<span class="tag lo">历史低价</span>');
  if (o.pp && o.pp > o.p) tags.push(`<span class="tag drop">刚降 ${usd(o.pp - o.p)}</span>`);
  const avail = o.z.filter((z) => z[1]).length;
  if (o.z.length > 2 && avail > 0 && avail <= 2 && !best.fit) tags.push(`<span class="tag few">仅剩 ${avail} 个尺码</span>`);
  if (best.fit) tags.push(`<span class="tag">${esc(best.fit.slice(0, 4).join("/"))}${S.catId === "ski" || S.catId === "pole" ? "cm" : ""} 有货</span>`);
  if (o.cd !== "new") tags.push(`<span class="tag cond">${COND[o.cd]}</span>`);
  if (fam.bd) tags.push('<span class="tag">含固定器</span>');
  const meta = metaLine(fam, o);
  return `<article class="card" data-k="${esc(fam.k)}">
    <div class="card-img">${fam.img ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(thumb(fam.img))}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'noimg',textContent:'🎿'}))">` : '<span class="noimg">🎿</span>'}${off >= 5 ? `<span class="off">-${off}%</span>` : ""}</div>
    <div class="card-body">
      <div class="card-brand">${brandLabel(fam.b)} <span class="g g-${fam.g}">${GENDER[fam.g]}</span></div>
      <h3 class="card-model">${esc(fam.m)}</h3>
      <div class="card-meta">${esc(meta) || "&nbsp;"}</div>
      <div class="price"><b>${usd(best.p)}</b>${r.msrp && r.msrp > best.p + 1 ? `<s>${usd(r.msrp)}</s>` : ""}</div>
      <div class="tags">${tags.join("")}</div>
      <div class="card-foot"><span>${stores > 1 ? stores + " 家有售 · " : ""}${esc(retName(o.r))}</span>${stores > 1 && maxP > best.p * 1.02 ? `<span>最高 ${usd(maxP)}</span>` : ""}</div>
    </div></article>`;
}
function renderGrid() {
  const rs = results();
  $("#count").textContent = `${rs.length.toLocaleString()} 款 · ${Object.keys(S.cat.retailers).length} 家网站`;
  const grid = $("#grid");
  if (!rs.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">没有符合条件的${esc(catName(S.catId))}。试试放宽筛选条件，或者 <button class="link-btn" data-act="reset">重置筛选</button>。</div>`;
    $("#more").innerHTML = "";
    return;
  }
  grid.innerHTML = rs.slice(0, S.shown).map(card).join("");
  $("#more").innerHTML = rs.length > S.shown ? `<button class="btn-plain load-more" data-act="more">再显示 ${Math.min(PAGE, rs.length - S.shown)} 款（共 ${rs.length}）</button>` : "";
}
function refresh(resetPage = true) {
  if (resetPage) S.shown = PAGE;
  saveF();
  renderFilters();
  renderGrid();
}
async function renderDeals() {
  const cat = S.catId;
  if (!S.data[cat]) {
    $("#app").innerHTML = `<div class="page">${catBar()}<div class="loading">正在加载 ${esc(catName(cat))}…</div></div>`;
    try { S.data[cat] = await api("/api/catalog?cat=" + enc(cat)); } catch (e) {
      $("#app").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
      return;
    }
    if (S.catId !== cat || S.route !== "deals") return;
  }
  S.cat = S.data[cat];
  S.f = loadF(cat);
  if (!S.cat.families.length) {
    const any = S.cats.some((c) => c.fams);
    $("#app").innerHTML = `<div class="page">${catBar()}<div class="section" style="margin:40px auto;max-width:620px;text-align:center">
      ${any ? `<h3>${esc(catName(cat))}还没有数据</h3><p class="muted">可能是还没抓过这个分类。</p>` :
        `<h2>👋 欢迎使用滑雪装备小帮手</h2><p class="muted">还没有数据。点下面的按钮，从各家美国雪具网站抓取价格（全品类约 20–30 分钟）。</p>`}
      <button class="btn" data-act="crawl">${any ? "抓取这个分类" : "开始第一次抓取"}</button></div></div>`;
    return;
  }
  $("#app").innerHTML = dealsShell();
  refresh(false);
}
function bindDeals() {
  const app = $("#app");
  const toggle = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  app.addEventListener("click", (e) => {
    const t = e.target.closest("[data-g],[data-off],[data-bind],[data-feat],[data-size],[data-din],[data-flex],[data-last],[data-act],.card");
    if (!t || S.route !== "deals") return;
    const d = t.dataset;
    if (d.g) { S.g.gender = d.g; $$("#seg-g button").forEach((b) => b.classList.toggle("on", b === t)); refresh(); }
    else if (d.off !== undefined) { S.f.minOff = +d.off; $$("#chips-off .chip").forEach((b) => b.classList.toggle("on", b === t)); refresh(); }
    else if (d.bind) { S.f.bind = d.bind; $$("#chips-bind .chip").forEach((b) => b.classList.toggle("on", b === t)); refresh(); }
    else if (d.feat) { S.f.feats = toggle(S.f.feats, d.feat); refresh(); }
    else if (d.size) { S.f.sizes = toggle(S.f.sizes || [], d.size); refresh(); }
    else if (d.din) { S.f.dinSel = toggle(S.f.dinSel, d.din); t.classList.toggle("on"); refresh(); }
    else if (d.flex) { S.f.flexSel = toggle(S.f.flexSel, d.flex); t.classList.toggle("on"); refresh(); }
    else if (d.last) { S.f.lastSel = toggle(S.f.lastSel, d.last); t.classList.toggle("on"); refresh(); }
    else if (d.act === "more") { S.shown += PAGE; renderGrid(); }
    else if (d.act === "reset") { S.f = defaultF(S.catId); S.q = ""; $("#q").value = ""; saveF(); renderDeals(); }
    else if (d.act === "expand") { const el = $("#" + d.target); el.dataset.expanded = el.dataset.expanded === "1" ? "0" : "1"; renderFilters(); }
    else if (d.act === "more-sizes") { S.moreSizes = !S.moreSizes; renderFilters(); }
    else if (d.act === "close-tip") { localStorage.setItem("sd.tip", String(new Date().getMonth())); $("#tip")?.remove(); }
    else if (d.act === "open-sizer") { location.hash = "#/guide"; setTimeout(() => $("#sizer")?.scrollIntoView({ behavior: "smooth" }), 80); }
    else if (d.act === "crawl") startCrawl(S.cats.some((c) => c.fams) ? [S.catId] : null);
    else if (t.classList.contains("card")) openModel(d.k);
  });
  app.addEventListener("change", (e) => {
    if (S.route !== "deals") return;
    const t = e.target, d = t.dataset;
    if (t.id === "inc-unisex") S.g.incUnisex = t.checked;
    else if (t.id === "inStock") S.f.inStock = t.checked;
    else if (t.id === "sort") S.f.sort = t.value;
    else if (d.type) S.f.types = toggle(S.f.types, d.type);
    else if (d.year) S.f.years = toggle(S.f.years, d.year);
    else if (d.brand) S.f.brands = toggle(S.f.brands, d.brand);
    else if (d.retailer) S.f.retailers = toggle(S.f.retailers, d.retailer);
    else if (d.cond) S.f.conds = toggle(S.f.conds, d.cond);
    else return;
    refresh();
  });
  // 状态立即更新，只把“重新渲染”做防抖 —— 否则快速切换两个输入框时，前一个框的值会丢
  const onRange = debounce(() => refresh(), 300);
  const RANGE = ["lenMin", "lenMax", "priceMin", "priceMax", "waistMin", "waistMax", "mondo", "brakeFor"];
  app.addEventListener("input", (e) => {
    if (S.route === "deals" && RANGE.includes(e.target.id)) {
      S.f[e.target.id] = e.target.value.trim();
      onRange();
    }
  });
}

// ================================================================ 详情抽屉
function showDrawer(html) {
  $("#drawer-body").innerHTML = html;
  $("#drawer").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}
function closeDrawer() {
  $("#drawer").classList.add("hidden");
  document.body.style.overflow = "";
  S.detail = null;
}
async function openModel(key) {
  showDrawer('<div class="loading">加载中…</div>');
  try {
    const [d, intl] = await Promise.all([api("/api/model?key=" + enc(key)), api("/api/intl?key=" + enc(key))]);
    const cat = d.category || "ski";
    if (!S.data[cat]) S.data[cat] = await api("/api/catalog?cat=" + enc(cat));  // 从关注页打开别的分类时，要有那个分类的零售商信息
    S.detail = { d, intl, key, watchOpen: false, cat: S.data[cat] };
    renderDetail();
    if (!intl.jp_query) refreshJP(true);  // 第一次打开这个型号：自动查一次日本价格
  } catch (e) {
    showDrawer(`<div class="empty">加载失败：${esc(e.message)}</div>`);
  }
}
const dRet = (id) => S.detail?.cat?.retailers[id] || {};
const dRetName = (id) => dRet(id).name || id;
function lineChart(series, w = 840, h = 230) {
  const all = series.flatMap((s) => s.pts);
  const days = [...new Set(all.map((p) => p[0]))].sort();
  if (days.length < 2) return `<div class="note">价格记录从 ${days[0] || "今天"} 开始。工具每天更新一次（价格有变化才记录），过几天这里就会出现价格走势图，也能判断“折扣”是不是真的。</div>`;
  const t = (d) => new Date(d + "T00:00:00").getTime();
  const x0 = t(days[0]), x1 = t(days[days.length - 1]);
  const ys = all.map((p) => p[1]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.12 || y1 * 0.05 || 10; y0 -= pad; y1 += pad;
  const L = 56, R = 12, T = 10, B = 26;
  const X = (d) => L + ((t(d) - x0) / (x1 - x0)) * (w - L - R);
  const Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (h - T - B);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = y0 + ((y1 - y0) * i) / 4, y = Y(v);
    g += `<line x1="${L}" x2="${w - R}" y1="${y}" y2="${y}" stroke="currentColor" opacity=".08"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="currentColor" opacity=".55">${usd(v)}</text>`;
  }
  [days[0], days[Math.floor(days.length / 2)], days[days.length - 1]].forEach((d, i) => {
    g += `<text x="${X(d)}" y="${h - 6}" text-anchor="${["start", "middle", "end"][i]}" font-size="11" fill="currentColor" opacity=".55">${d.slice(5)}</text>`;
  });
  const paths = series.map((s) => {
    const pts = s.pts.slice().sort((a, b) => a[0].localeCompare(b[0]));
    if (!pts.length) return "";
    let dpath = `M${X(pts[0][0]).toFixed(1)},${Y(pts[0][1]).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) dpath += ` H${X(pts[i][0]).toFixed(1)} V${Y(pts[i][1]).toFixed(1)}`;  // 阶梯线
    return `<path d="${dpath}" fill="none" stroke="${s.color}" stroke-width="${s.w || 1.6}" opacity="${s.op || 0.9}"/>`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">${g}${paths}</svg>
    <div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}
function sizeChips(o, cat) {
  const lengthBased = cat === "ski" || cat === "pole";
  const sizes = (o.sizes || []).map((s) => ({ ...s, key: lengthBased ? s.cm : (s.n || s.label) })).filter((s) => s.key);
  if (!sizes.length) return '<span class="muted small">均码 / 未提供</span>';
  if (lengthBased) sizes.sort((a, b) => a.key - b.key);
  else if (cat === "boot") sizes.sort((a, b) => parseFloat(a.key) - parseFloat(b.key));
  else if (cat === "binding") sizes.sort((a, b) => parseInt(a.key, 10) - parseInt(b.key, 10));
  else {
    const order = sortSizes([...new Set(sizes.map((s) => String(s.key).toUpperCase()))]);
    sizes.sort((a, b) => order.indexOf(String(a.key).toUpperCase()) - order.indexOf(String(b.key).toUpperCase()));
  }
  const f = S.catId === cat && S.f ? S.f : defaultF(cat);
  const active = sizeFilterActive(cat, f);
  return sizes.map((s) => {
    const mine = s.a && active && sizeMatch(cat, f, [s.key, 1, s.p]);
    const priceNote = s.p && Math.abs(s.p - o.p) > 1 ? ` title="${usd(s.p)}"` : "";
    return `<span class="sz ${s.a ? "a" : "x"} ${mine ? "mine" : ""}"${priceNote}>${esc(s.key)}</span>`;
  }).join("");
}
function specLine(d) {
  const sp = d.sp || {}, cat = d.category, out = [];
  if (cat === "ski") {
    if (d.t) out.push(TYPE[d.t]);
    if (d.w) out.push(`腰宽 ${d.w}mm`);
    out.push(d.bd ? `含固定器套装${d.bn ? "：" + d.bn : ""}` : "板身（不含固定器）");
  } else if (cat === "binding") {
    if (sp.din) out.push(`DIN ${sp.din[0]}–${sp.din[1]}`); else if (sp.din_max) out.push(`最大 DIN ${sp.din_max}`);
    if (sp.brakes) out.push(`刹车宽度 ${sp.brakes.join(" / ")} mm`);
  } else if (cat === "boot") {
    if (sp.flex) out.push(`硬度 Flex ${sp.flex}`);
    if (sp.last) out.push(typeof sp.last === "number" ? `楦宽 ${sp.last}mm` : sp.last);
  } else if (cat === "backpack" && sp.volume) out.push(`容量 ${sp.volume}L`);
  out.push(...(d.ft || []).map(featLabel));
  return out;
}
function detailHead(d) {
  const newAvail = d.offers.filter((o) => o.cd === "new" && o.a);
  const pool = newAvail.length ? newAvail : d.offers;
  const best = pool.reduce((a, b) => (b.p < a.p ? b : a));
  const msrp = d.msrp[String(best.y)] ?? d.msrp.None;
  const tax = (S.meta?.settings?.sales_tax_pct ?? 6.25) / 100;
  const years = [...new Set(d.offers.map((o) => o.y).filter(Boolean))].sort((a, b) => b - a);
  const w = d.watch, cat = d.category;
  const lengthBased = cat === "ski" || cat === "pole";
  const f = S.catId === cat && S.f ? S.f : {};
  const defaultSize = lengthBased ? (f.lenMin && f.lenMin === f.lenMax ? f.lenMin : "")
    : cat === "boot" ? (f.mondo || "") : ((f.sizes || []).length === 1 ? f.sizes[0] : "");
  const watchUI = S.detail.watchOpen ? `
    <div class="watchbox">目标价 $<input id="w-target" inputmode="decimal" value="${esc(w?.target_price ?? Math.round(best.p * 0.9))}">
      ${lengthBased ? "长度" : "尺码"} <input id="w-size" placeholder="任意" value="${esc(lengthBased ? (w?.length_cm ?? defaultSize) : (w?.size_label ?? defaultSize))}"> ${lengthBased ? "cm" : ""}
      年份 <select id="w-year"><option value="">任意</option>${years.map((y) => `<option ${w?.year === y ? "selected" : ""}>${y}</option>`).join("")}</select>
      <button class="btn btn-sm" data-act="watch-save">保存</button> <button class="btn-plain btn-sm" data-act="watch-cancel">取消</button></div>
    <div class="note">每次更新数据后检查：降到目标价、或比上次便宜 3% 以上，就会在「关注」页提醒并弹 Windows 通知。</div>` :
    w ? `<div class="watchbox">★ 已关注${w.target_price ? " · 目标 " + usd(w.target_price) : ""}${w.length_cm ? " · " + w.length_cm + "cm" : ""}${w.size_label ? " · 尺码 " + esc(w.size_label) : ""}${w.year ? " · " + w.year + " 款" : ""}
      <button class="btn-plain btn-sm" data-act="watch-open">修改</button> <button class="btn-plain btn-sm" data-act="watch-del">取消关注</button></div>` :
      `<button class="btn btn-sm" data-act="watch-open">☆ 关注降价</button>`;
  return `<div class="dhead">
    <div class="img">${d.img ? `<img referrerpolicy="no-referrer" src="${esc(thumb(d.img))}" alt="">` : "🎿"}</div>
    <div style="flex:1;min-width:0">
      <div class="muted">${brandLabel(d.b)} · ${esc(catName(cat))}</div>
      <h2>${esc(d.m)}</h2>
      <div class="kv"><span class="g g-${d.g}">${GENDER[d.g]}</span>${specLine(d).map((x) => `<span>${esc(x)}</span>`).join("")}${years.length ? `<span>年份：${years.join(" / ")}</span>` : ""}</div>
      <div class="best"><b>${usd(best.p)}</b><span>${esc(dRetName(best.r))}${best.y ? " · " + best.y + " 款" : ""}${best.cd !== "new" ? " · " + COND[best.cd] : ""}</span>
        ${msrp && msrp > best.p + 1 ? `<s class="muted">${usd(msrp)}</s><span class="save">省 ${usd(msrp - best.p)}（-${Math.round((1 - best.p / msrp) * 100)}%）</span>` : ""}</div>
      <div class="muted small">含 ${(tax * 100).toFixed(2).replace(/\.?0+$/, "")}% 销售税约 ${usd(best.p * (1 + tax))} · 原价取各店标注原价的众数/品牌官网价</div>
      <div style="margin-top:10px">${watchUI}</div>
    </div></div>`;
}
function offersTable(d) {
  const minNew = Math.min(...d.offers.filter((o) => o.cd === "new" && o.a).map((o) => o.p));
  const unit = d.category === "ski" || d.category === "pole" ? "长度 (cm)" : d.category === "binding" ? "刹车宽度" : "尺码";
  const rows = d.offers.map((o) => {
    const r = dRet(o.r);
    const flags = [o.lo && '<span class="tag lo">历史低价</span>', o.pp && o.pp > o.p && `<span class="tag drop">${o.pd?.slice(5)} 从 ${usd(o.pp)} 降价</span>`,
      o.pp && o.pp < o.p && `<span class="tag">${o.pd?.slice(5)} 从 ${usd(o.pp)} 涨价</span>`,
      o.inf && '<span class="tag inf" title="这家标的原价明显高于其他网站">原价虚高</span>'].filter(Boolean).join(" ");
    return `<tr class="${o.p === minNew && o.a && o.cd === "new" ? "best-row" : ""} ${o.a ? "" : "dim"}">
      <td><b>${esc(r.name || o.r)}</b> <span class="tier ${r.tier}">${TIER[r.tier] || ""}</span><div class="small muted" title="${esc(o.title)}">${esc(o.title).slice(0, 70)}</div></td>
      <td class="num">${o.y || "—"}${o.cd !== "new" ? `<div><span class="tag cond">${COND[o.cd]}</span></div>` : ""}</td>
      <td class="num"><b>${usd(o.p)}</b>${o.c ? `<div class="small muted"><s>${usd(o.c)}</s></div>` : ""}</td>
      <td class="num">${o.off ? `<span class="pct cheaper">-${o.off}%</span>` : '<span class="muted">—</span>'}</td>
      <td>${sizeChips(o, d.category)}${flags ? `<div style="margin-top:3px">${flags}</div>` : ""}</td>
      <td class="small muted">${o.a ? "有货" : "无货"}<div>${ago(o.last_seen)}</div></td>
      <td><a href="${esc(o.u)}" target="_blank" rel="noopener noreferrer">去看看 ↗</a></td></tr>`;
  }).join("");
  return `<div class="section"><h3>各网站报价 <span class="sub">绿色行 = 全新有货最低价；蓝色 = 有货，深蓝 = 符合你筛选的尺码，划线 = 无货</span></h3>
    <div style="overflow-x:auto"><table><thead><tr><th>网站</th><th>年份</th><th>价格</th><th>折扣</th><th>${unit}</th><th>库存</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    ${priceMatchTip(d, minNew)}</div>`;
}
function priceMatchTip(d, minNew) {
  // 最低价不在这些店，但这些店也卖这款、且接受价格匹配 → 可以拿最低价去谈（还能享受它们的退货/会员福利）
  if (!isFinite(minNew)) return "";
  const cheapest = d.offers.find((o) => o.p === minNew && o.a && o.cd === "new");
  const seen = new Set();
  const tips = d.offers.filter((o) => o.a && o.cd === "new" && o.p > minNew + 1 && dRet(o.r).price_match && !seen.has(o.r) && seen.add(o.r))
    .map((o) => {
      const est = o.r === "evo" ? ` —— 按规则约可拿到 ${usd(minNew * 0.95)}` : "";
      return `<li><b>${esc(dRetName(o.r))}</b>（现价 ${usd(o.p)}）：${esc(dRet(o.r).price_match)}${est}</li>`;
    });
  if (!tips.length) return "";
  return `<div class="tip" style="margin:12px 0 0"><span>💡</span><div><b>可以试试价格匹配</b>：最低价是 ${esc(dRetName(cheapest?.r))} 的 ${usd(minNew)}，下面这些店也有货并接受价格匹配（适合想在大店买、享受更好退货政策时）：
    <ul class="plain">${tips.join("")}</ul><span class="small muted">价格匹配规则来自各店公开政策（2026-09 查询），下单前请再确认。</span></div></div>`;
}
function bindingSection(d) {
  // 固定器：套装 → 和“板身 + 同款固定器分开买”对比；板身 → 推荐刹车宽度合适的固定器
  if (d.category !== "ski") return "";
  const pk = d.package, sg = d.binding_suggest;
  if (d.bd && pk) {
    let body;
    if (pk.separate_total != null) {
      const cheaper = pk.diff > 0;
      body = `<div class="intl-row"><span class="flag">套装</span><span class="big">${usd(pk.package.p)}</span><span class="muted">${esc(dRetName(pk.package.r))}</span><span></span><span></span></div>
        <div class="intl-row"><span class="flag">分开买</span><span class="big">${usd(pk.separate_total)}</span>
          <span class="muted">板身 ${usd(pk.flat.p)}（${esc(dRetName(pk.flat.r))}）+ 固定器 ${usd(pk.binding.p)}（${esc(pk.binding.b)} ${esc(pk.binding.m)}）</span>
          <span><button class="link-btn small" data-open="${esc(pk.flat.k)}">看板身</button> · <button class="link-btn small" data-open="${esc(pk.binding.k)}">看固定器</button></span>
          <span class="pct ${cheaper ? "pricier" : "cheaper"}">${cheaper ? "贵 " + usd(pk.diff) : "省 " + usd(-pk.diff)}</span></div>
        <div class="note">${cheaper ? "套装更划算" : "分开买更便宜"}（还没算安装费：买套装通常免费安装，分开买再找店安装一般 $50–80）。</div>`;
    } else {
      body = `<div class="note">${pk.note ? esc(pk.note) + "。" : ""}${pk.flat ? `板身单卖最低 ${usd(pk.flat.p)}（${esc(dRetName(pk.flat.r))}）。` : ""}${pk.binding ? `同款固定器单卖最低 ${usd(pk.binding.p)}。` : "没找到同款固定器单卖的报价，无法算“分开买”总价。"}</div>`;
    }
    return `<div class="section"><h3>固定器：套装 vs 分开买 <span class="sub">套装里是 ${esc(pk.binding_name || "（未识别）")}</span></h3>${body}</div>`;
  }
  if (!d.bd && sg) {
    if (!sg.groups.length) return `<div class="section"><h3>搭配固定器</h3><div class="note">${esc(sg.note || "暂时没有刹车宽度合适、有货的固定器报价。")}</div></div>`;
    return `<div class="section"><h3>搭配固定器 <span class="sub">刹车宽度 ${sg.waist}–${sg.waist + 20}mm${sg.touring ? " · 登山板推荐登山/多标准固定器" : ""}，有货的最低价</span></h3>
      ${sg.groups.map((g) => `<div style="margin:6px 0"><b class="small">${esc(g.label)}</b><div class="chips" style="margin-top:4px">${g.items.map((b) =>
        `<button class="chip" data-open="${esc(b.k)}">${esc(b.b)} ${esc(b.m)} · 刹车 ${b.brakes.join("/")} · ${usd(b.p)}</button>`).join("")}</div></div>`).join("")}
      <div class="note">选固定器：刹车宽度 ≥ 腰宽（最多宽 15–20mm）；DIN 设定值落在固定器范围中间；GripWalk 鞋底要配标 GW/MNC 的固定器。雪具店装固定器通常 $50–80（和雪板一起买常免费）。</div></div>`;
  }
  return "";
}
function historySection(d) {
  const top = d.offers.filter((o) => o.history.length).sort((a, b) => a.p - b.p).slice(0, 6);
  const series = [];
  if (d.daily_min.length) series.push({ name: "全网最低（全新有货）", color: "currentColor", w: 2.6, op: 0.85, pts: d.daily_min });
  top.forEach((o, i) => series.push({ name: `${dRetName(o.r)}${o.y ? " " + o.y : ""}`, color: COLORS[i % COLORS.length], pts: o.history.filter((h) => h[1]).map((h) => [h[0], h[1]]) }));
  return `<div class="section"><h3>价格走势 <span class="sub">价格有变化才记录，按天画阶梯线</span></h3>${lineChart(series)}</div>`;
}
function intlSection() {
  const { intl, d } = S.detail;
  const s = intl.summary, fx = intl.fx.rates;
  const pct = (v) => (v == null ? "" : v === 0 ? '<span class="pct">持平</span>' : v < 0 ? `<span class="pct cheaper">便宜 ${-v}%</span>` : `<span class="pct pricier">贵 ${v}%</span>`);
  const us = s.US ? `<div class="intl-row"><span class="flag">美国</span><span class="big">${usd(s.US.usd)}</span><span class="muted">≈ ${rmb(s.US.cny)} · ${yen(s.US.jpy)}</span><span>${esc(dRetName(s.US.retailer))}${s.US.year ? " · " + s.US.year + " 款" : ""}</span><span class="muted small">基准</span></div>`
    : `<div class="intl-row"><span class="flag">美国</span><span class="muted">暂无全新有货报价</span></div>`;
  const q = intl.jp_query;
  const jpState = S.detail.jpLoading ? "正在查询价格.com…" : !q ? "还没查询" : q.status !== "ok" ? "查询失败：" + esc(q.status) : "没有找到匹配的日本报价";
  const row = (flag, x) => x ? `<div class="intl-row"><span class="flag">${flag}</span><span class="big">${money(x.price, x.currency)}</span>
      <span class="muted">≈ ${usd(x.usd)} · ${rmb(x.cny)}</span>
      <span>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.shop || "链接")} ↗</a>` : esc(x.shop || "")}${x.year ? ` · ${x.year} 款` : ""}${x.year && s.US?.year && x.year !== s.US.year ? ' <span class="tag">年份不同</span>' : ""}${x.manual ? ' <span class="tag">手动记录</span>' : ""}</span>
      <span>${pct(x.vs_us_pct)}</span></div>` : "";
  const jp = row("日本", s.JP) || `<div class="intl-row"><span class="flag">日本</span><span class="muted" style="grid-column:2/-1">${jpState}</span></div>`;
  const cn = row("中国", s.CN) || `<div class="intl-row"><span class="flag">中国</span><span class="muted" style="grid-column:2/-1">暂无记录 —— 国内电商需要登录、反爬严格，请点下面的链接查看，再“记一笔参考价”</span></div>`;
  const jpEntries = intl.entries.filter((e) => e.country === "JP" && !e.manual);
  const manual = intl.entries.filter((e) => e.manual);
  const links = (arr) => arr.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.name)}</a>`).join("");
  const years = [...new Set(d.offers.map((o) => o.y).filter(Boolean))].sort((a, b) => b - a);
  return `<div class="section"><h3>国际比价 <span class="sub">1 美元 = ${fx.JPY?.toFixed(2)} 日元 = ${fx.CNY?.toFixed(4)} 人民币（${esc(intl.fx.source)}）</span></h3>
    ${us}${jp}${cn}
    <div class="linkrow"><button class="btn-plain btn-sm" data-act="jp-refresh" ${S.detail.jpLoading ? "disabled" : ""}>🔄 查日本最新价</button>
      <span class="muted small">${q ? `上次查询 ${ago(q.fetched_at)}，找到 ${q.n} 条同款` : ""}</span></div>
    ${jpEntries.length ? `<details><summary>日本报价明细（${jpEntries.length} 条，来自价格.com，含税价）</summary>
      <table style="margin-top:6px"><thead><tr><th>店铺</th><th>商品标题</th><th>年份</th><th>价格</th><th>≈ 美元</th></tr></thead><tbody>
      ${jpEntries.map((e) => `<tr><td class="small">${esc(e.shop || "")}</td><td class="small"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title).slice(0, 80)}</a>${e.match_score < 1 ? ' <span class="tag" title="标题里有额外的版本词，可能不是完全同款">待核对</span>' : ""}</td><td class="num">${e.year || "—"}</td><td class="num">${yen(e.price)}</td><td class="num">${usd(e.usd)}</td></tr>`).join("")}
      </tbody></table></details>` : ""}
    <div class="linkrow"><b class="small">中国 · 去看看：</b>${links(intl.links.CN || [])}</div>
    <div class="linkrow"><b class="small">日本 · 其他网站：</b>${links((intl.links.JP || []).slice(1))}</div>
    <div class="linkrow"><b class="small">美国 · 无法自动抓取的网站：</b>${links(intl.links.US || [])}</div>
    <details ${manual.length ? "open" : ""}><summary>记一笔参考价（手动）${manual.length ? `· 已记录 ${manual.length} 条` : ""}</summary>
      ${manual.length ? `<table style="margin-top:6px"><tbody>${manual.map((e) => `<tr><td>${e.country === "CN" ? "中国" : "日本"} · ${esc(e.shop || "")}</td><td class="num">${money(e.price, e.currency)}</td><td class="num muted">≈ ${usd(e.usd)}</td><td class="small muted">${e.year || ""} ${e.bindings ? "含固定器" : ""} ${esc(e.note || "")}</td><td>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">链接</a>` : ""}</td><td><button class="link-btn small" data-act="intl-del" data-id="${e.id}">删除</button></td></tr>`).join("")}</tbody></table>` : ""}
      <div class="form">
        <select id="m-country"><option value="CN">中国</option><option value="JP">日本</option></select>
        <input id="m-shop" placeholder="平台/店铺，如 天猫旗舰店">
        <input id="m-price" inputmode="decimal" placeholder="价格">
        <select id="m-cur"><option value="CNY">人民币 CNY</option><option value="JPY">日元 JPY</option><option value="USD">美元 USD</option></select>
        <select id="m-year"><option value="">年份（可选）</option>${[season(), ...years].filter((v, i, a) => a.indexOf(v) === i).map((y) => `<option>${y}</option>`).join("")}</select>
        ${d.category === "ski" ? '<select id="m-bind"><option value="">固定器？</option><option value="0">不含固定器</option><option value="1">含固定器</option></select>' : ""}
        <input id="m-url" placeholder="商品链接（可选）">
        <input id="m-note" placeholder="备注（可选）">
        <button class="btn btn-sm" data-act="intl-add">保存</button>
      </div></details>
    <div class="note">美国：${esc(intl.notes.US)}<br>日本：${esc(intl.notes.JP)}<br>中国：${esc(intl.notes.CN)}<br>日本价格来自价格.com 的自动匹配（品牌+型号+性别${d.category === "ski" ? "+是否含固定器" : ""}都要一致），仍建议点链接核对年份和尺码。</div>
  </div>`;
}
function siblingsSection(d) {
  if (!d.siblings.length) return "";
  return `<div class="section"><h3>相近型号 <span class="sub">同型号的其他版本（女款/儿童/${d.category === "ski" ? "含固定器/" : ""}Ti 版等）</span></h3>
    <div class="chips">${d.siblings.map((s) => `<button class="chip" data-open="${esc(s.k)}">${esc(s.m)} · ${GENDER[s.g]}${s.bd ? " · 含固定器" : ""} · ${usd(s.p)} 起</button>`).join("")}</div></div>`;
}
function renderDetail() {
  const { d } = S.detail;
  $("#drawer-body").innerHTML = detailHead(d) + offersTable(d) + bindingSection(d) + intlSection() + historySection(d) + siblingsSection(d) +
    `<div class="note" style="margin:0 24px 30px">分组依据：分类 + 品牌 + 型号 + 性别大类${d.category === "ski" ? " + 是否含固定器" : ""}。同一型号不同年份放在一起，年份单独标注；如果发现分错了，可以在 skideals/normalize.py 里调整规则。</div>`;
}
async function refreshJP(auto = false) {
  const key = S.detail?.key;
  if (!key) return;
  S.detail.jpLoading = true;
  renderDetail();
  try {
    const intl = await api("/api/intl/refresh", { method: "POST", body: { key } });
    if (S.detail?.key === key) { S.detail.intl = intl; }
  } catch (e) { if (!auto) toast("查询失败：" + e.message); }
  if (S.detail?.key === key) { S.detail.jpLoading = false; renderDetail(); }
}
function bindDrawer() {
  $("#drawer").addEventListener("click", async (e) => {
    const t = e.target.closest("[data-close],[data-act],[data-open]");
    if (!t) return;
    if (t.hasAttribute("data-close")) return closeDrawer();
    if (t.dataset.open) return openModel(t.dataset.open);
    const key = S.detail?.key;
    const act = t.dataset.act;
    try {
      if (act === "jp-refresh") await refreshJP();
      else if (act === "watch-open") { S.detail.watchOpen = true; renderDetail(); }
      else if (act === "watch-cancel") { S.detail.watchOpen = false; renderDetail(); }
      else if (act === "watch-save") {
        const lengthBased = ["ski", "pole"].includes(S.detail.d.category);
        const size = $("#w-size").value.trim();
        await api("/api/watchlist", { method: "POST", body: { key, target_price: $("#w-target").value, year: $("#w-year").value,
          length_cm: lengthBased ? size : "", size_label: lengthBased ? "" : size } });
        S.detail.d = await api("/api/model?key=" + enc(key));
        S.detail.watchOpen = false; renderDetail(); toast("已加入关注，降价时会提醒你"); loadMeta();
      } else if (act === "watch-del") {
        await api("/api/watchlist?key=" + enc(key), { method: "DELETE" });
        S.detail.d.watch = null; renderDetail(); toast("已取消关注"); loadMeta();
      } else if (act === "intl-add") {
        const bindSel = $("#m-bind");
        const body = { key, country: $("#m-country").value, shop: $("#m-shop").value, price: $("#m-price").value,
          currency: $("#m-cur").value, year: $("#m-year").value, url: $("#m-url").value, note: $("#m-note").value,
          bindings: !bindSel || bindSel.value === "" ? null : bindSel.value === "1" };
        S.detail.intl = await api("/api/intl/manual", { method: "POST", body });
        renderDetail(); toast("已记录");
      } else if (act === "intl-del") {
        S.detail.intl = await api(`/api/intl/entry/${t.dataset.id}?key=${enc(key)}`, { method: "DELETE" });
        renderDetail();
      }
    } catch (err) { toast(err.message); }
  });
  $("#drawer").addEventListener("change", (e) => {  // 选日本时默认币种改成日元
    if (e.target.id === "m-country") $("#m-cur").value = e.target.value === "JP" ? "JPY" : "CNY";
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").classList.contains("hidden")) closeDrawer(); });
}

// ================================================================ 关注页
async function renderWatch() {
  $("#app").innerHTML = '<div class="page"><div class="loading">加载中…</div></div>';
  const [list, alerts] = await Promise.all([api("/api/watchlist"), api("/api/alerts")]);
  const rows = list.map((w) => {
    const f = w.family, b = w.best;
    const gap = b && w.target_price ? b.usd - w.target_price : null;
    const cat = w.model_key.split("|")[0];
    return `<tr class="clickable" data-open="${esc(w.model_key)}">
      <td>${f?.img ? `<img referrerpolicy="no-referrer" src="${esc(thumb(f.img))}" style="width:56px;height:42px;object-fit:contain;background:#fff;border-radius:6px">` : ""}</td>
      <td><b>${esc(w.label)}</b> <span class="muted small">${esc(catName(cat))}</span>${f ? ` <span class="g g-${f.g}">${GENDER[f.g]}</span>` : ' <span class="tag">已下架</span>'}<div class="small muted">${w.length_cm ? w.length_cm + "cm · " : ""}${w.size_label ? "尺码 " + esc(w.size_label) + " · " : ""}${w.year ? w.year + " 款 · " : ""}关注于 ${w.created_at.slice(0, 10)}</div></td>
      <td class="num">${w.target_price ? usd(w.target_price) : "—"}</td>
      <td class="num">${b ? `<b>${usd(b.usd)}</b><div class="small muted">${esc(b.retailer)}</div>` : '<span class="muted">暂无有货</span>'}</td>
      <td class="num">${gap == null ? "" : gap <= 0 ? '<span class="pct cheaper">已达到 ✓</span>' : `<span class="muted">还差 ${usd(gap)}</span>`}</td>
      <td>${b ? `<a href="${esc(b.url)}" target="_blank" rel="noopener noreferrer" data-stop>去看看 ↗</a>` : ""}</td></tr>`;
  }).join("");
  $("#app").innerHTML = `<div class="page">
    <div class="section" style="margin:0 0 16px"><h3>我的关注 <span class="sub">在任意商品详情里点“☆ 关注降价”添加；每次更新数据后自动检查</span></h3>
      ${list.length ? `<table><thead><tr><th></th><th>商品</th><th>目标价</th><th>当前最低</th><th></th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">还没有关注的商品。去「找折扣」点开任意商品，点“☆ 关注降价”。</div>'}</div>
    <div class="section" style="margin:0"><h3>降价提醒记录</h3>
      ${alerts.length ? `<table><tbody>${alerts.map((a) => `<tr><td class="small muted" style="white-space:nowrap">${a.created_at.replace("T", " ").slice(0, 16)}</td><td>${a.seen ? "" : '<span class="badge">新</span> '}${esc(a.message)}</td><td>${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">去看看 ↗</a>` : ""}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">暂无提醒</div>'}</div>
  </div>`;
  if (alerts.some((a) => !a.seen)) { await api("/api/alerts/seen", { method: "POST" }); loadMeta(); }
}

// ================================================================ 可信度页
function reportHTML(rep) {
  const icon = { pass: "✓", warn: "⚠", fail: "✗", info: "·" };
  const f = rep.facts || {};
  return `<div class="report">
    <div style="text-align:center"><div class="score lv-${rep.level}"><b>${rep.score}</b><span class="small">/ 100</span></div>
      <div style="margin-top:8px"><span class="pill lv-${rep.level}">${esc(rep.level_label)}</span></div>
      <div class="small muted" style="margin-top:6px">${esc(rep.domain)}<br>检测于 ${esc((rep.checked_at || "").replace("T", " ").slice(0, 16))}</div></div>
    <div><ul class="signals">${rep.signals.map((s) => `<li><span class="s-${s.status}">${icon[s.status]}</span><span class="muted small">${s.points ? (s.points > 0 ? "+" : "") + s.points : ""}</span><b>${esc(s.label)}</b><span>${esc(s.detail)}</span></li>`).join("")}</ul>
      <div class="linkrow"><span class="small muted">人工复核：</span>${rep.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.name)}</a>`).join("")}</div>
      ${f.page?.policies && Object.keys(f.page.policies).length ? `<div class="linkrow"><span class="small muted">政策页面：</span>${Object.entries(f.page.policies).map(([k, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${{ returns: "退货", shipping: "运费", privacy: "隐私", terms: "条款" }[k]}</a>`).join("")}</div>` : ""}
    </div></div>`;
}
function ddHTML(r) {
  const d = r.dd;
  if (!d && !r.price_match) return "";
  const row = (k, v) => (v && !/^(unknown|not checked)/i.test(v) ? `<tr><td class="muted small" style="width:110px">${k}</td><td class="small">${esc(v)}</td></tr>` : "");
  const sub = d?.light ? "轻量核查：只有自动检测到的事实（域名、存档、登记地），还没做完整的人工调查"
    : "公开资料整理（2026-09），政策可能变化，下单前以官网为准";
  return `<div class="section" style="margin:6px 0 12px"><h3>${d?.light ? "核查记录" : "人工调查"} <span class="sub">${sub}</span>
      ${d?.verdict ? `<span class="tier ${d.verdict === "caution" ? "caution" : d.verdict}">${d.verdict === "caution" ? "⚠ 需谨慎" : TIER[d.verdict] || d.verdict}</span>` : ""}</h3>
    <table><tbody>${d ? row("总部", d.hq) + row("成立", d.founded) + row("实体店", d.physical_stores) + row("股权/经营", d.ownership_notes) +
      row("BBB", d.bbb) + row("Trustpilot", d.trustpilot) + row("常见投诉", d.complaints) + row("退货", d.returns) + row("运费", d.shipping) +
      row("价格匹配", r.price_match || d.price_match) + row("结论", d.verdict_reason) : row("价格匹配", r.price_match)}</tbody></table>
    ${d?.sources?.length ? `<div class="linkrow"><span class="small muted">来源：</span>${d.sources.slice(0, 8).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${i + 1}</a>`).join("")}</div>` : ""}</div>`;
}
function catsHTML(r) {
  const entries = Object.entries(r.cats || {});
  if (!entries.length) return "";
  return `<div class="linkrow"><span class="small muted">各分类最近一次抓取：</span>${entries.map(([c, v]) =>
    `<span class="tag ${v.status === "ok" ? "" : "drop"}" title="${esc(v.msg)} · ${esc((v.at || "").replace("T", " ").slice(0, 16))}">${esc(catName(c))} ${v.n}${v.status === "ok" ? "" : " ⚠"}</span>`).join("")}</div>`;
}
async function renderTrust() {
  $("#app").innerHTML = `<div class="page">
    <div class="section" style="margin:0 0 16px"><h3>检测一个网站是否可靠 <span class="sub">在网上看到“超低价雪具”？先把网址贴进来查一查</span></h3>
      <form class="checker" id="checker"><input id="check-url" placeholder="例如 https://www.some-ski-outlet.shop" autocomplete="off"><button class="btn">检测</button></form>
      <div id="check-result"></div>
      <div class="note">检测项：域名注册时间、网站历史存档（及是否中断）、HTTPS 证书、实体地址/电话/退货政策、Shopify 店铺登记地、是否全场半价以下、同款比正规店便宜一半以上、域名冒用品牌名或冒充已知零售商、可疑域名后缀。
        分数只是辅助判断——付款前请再看看 Trustpilot / Reddit 上的真实评价，并尽量用信用卡或 PayPal（有争议可以拒付）。</div></div>
    <div class="section" style="margin:0"><h3>已收录的网站 <span class="sub">只有这些经过核验的网站会被抓取价格；点击一行查看核验详情和各分类抓取情况</span>
      <button class="btn-plain btn-sm" data-act="recheck" style="margin-left:auto">重新核验全部</button></h3>
      <div id="retailer-table"><div class="loading">加载中…</div></div></div></div>`;
  const list = await api("/api/trust/retailers");
  const st = { ok: "✓ 正常", partial: "◐ 部分分类失败", blocked: "⚠ 被反爬拦截", robots: "⚠ robots 不允许", error: "✗ 出错" };
  $("#retailer-table").innerHTML = `<div style="overflow-x:auto"><table><thead><tr><th>网站</th><th>类型</th><th>可信度</th><th>域名注册</th><th>店铺登记地</th><th>抓取状态</th><th>商品数</th><th>最近更新</th></tr></thead><tbody>
    ${list.map((r, i) => {
      const rep = r.report, f = rep?.facts || {};
      const loc = r.meta ? [r.meta.city, r.meta.province].filter(Boolean).join(", ") : (f.page?.address || "");
      return `<tr class="clickable ${r.enabled ? "" : "dim"}" data-row="${i}">
        <td><b>${esc(r.name)}</b><div class="small"><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer" data-stop>${esc(r.url.replace(/^https?:\/\//, ""))}</a></div></td>
        <td><span class="tier ${r.tier}">${TIER[r.tier] || r.tier}</span></td>
        <td>${rep ? `<span class="pill lv-${rep.level}">${rep.score} · ${esc(rep.level_label)}</span>` : `<span class="muted small">${r.enabled ? "核验中…" : "未核验（已停用）"}</span>`}</td>
        <td class="small">${f.domain_created ? f.domain_created.slice(0, 4) + " 年" : "—"}${f.wayback_first ? `<div class="muted">存档自 ${String(f.wayback_first).slice(0, 4)}</div>` : ""}</td>
        <td class="small">${esc(loc) || "—"}</td>
        <td class="st ${r.status || ""}">${r.enabled ? st[r.status] || "未抓取" : "已停用"}${r.status && r.status !== "ok" ? `<div class="small muted">${esc(r.msg || "").slice(0, 60)}</div>` : ""}</td>
        <td class="num">${r.n ?? "—"}</td><td class="small muted">${ago(r.last_ok)}</td></tr>
        <tr class="hidden" id="rep-${i}"><td colspan="8">${catsHTML(r)}${ddHTML(r)}${rep ? reportHTML(rep) : "尚未核验"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}
function bindTrust() {
  $("#app").addEventListener("submit", async (e) => {
    if (e.target.id !== "checker") return;
    e.preventDefault();
    const url = $("#check-url").value.trim();
    if (!url) return;
    const box = $("#check-result");
    box.innerHTML = '<div class="loading">正在检测（查询域名注册信息、网站存档、页面内容…约 10–30 秒）</div>';
    try { box.innerHTML = reportHTML(await api("/api/trust/check", { method: "POST", body: { url } })); }
    catch (err) { box.innerHTML = `<div class="empty">检测失败：${esc(err.message)}</div>`; }
  });
  $("#app").addEventListener("click", async (e) => {
    if (S.route === "trust") {
      if (e.target.closest("[data-stop]")) return;
      const row = e.target.closest("tr[data-row]");
      if (row) $("#rep-" + row.dataset.row).classList.toggle("hidden");
      if (e.target.closest("[data-act=recheck]")) { await api("/api/trust/recheck-all", { method: "POST" }); toast("已在后台重新核验（约 2–4 分钟），稍后刷新本页"); }
    } else if (S.route === "watch") {
      if (e.target.closest("[data-stop]")) return;
      const row = e.target.closest("tr[data-open]");
      if (row) openModel(row.dataset.open);
    } else if (S.route === "guide") {
      const t = e.target.closest("[data-act]");
      if (t?.dataset.act === "size-calc") sizeCalc();
      if (t?.dataset.act === "size-apply") sizeApply();
      if (t?.dataset.act === "save-settings") saveSettings();
    }
  });
}

// ================================================================ 购买指南页（含尺码助手、装备怎么选、设置）
function sizeAdvice({ gender, height, weight, level, style, region }) {
  if (!height) return null;
  let base;
  if (gender === "kids") base = height + { beginner: -22, intermediate: -14, advanced: -8, expert: -4 }[level];
  else base = height + { beginner: -15, intermediate: -8, advanced: -3, expert: 2 }[level];
  base += { frontside: -4, all_mountain: 0, freeride: 5, park: -4, touring: -3, race: -2 }[style] || 0;
  if (weight && gender !== "kids") {
    const bmi = weight / (height / 100) ** 2;
    if (bmi > 27) base += 3;
    else if (bmi < 19) base -= 3;
  }
  const len = [Math.round(base - 3), Math.round(base + 3)];
  const W = {
    frontside: { east: [66, 80], west: [70, 84], japan: [70, 84] },
    race: { east: [64, 72], west: [64, 72], japan: [64, 72] },
    all_mountain: { east: [80, 92], west: [88, 100], japan: [90, 102] },
    freeride: { east: [95, 106], west: [100, 115], japan: [104, 120] },
    park: { east: [84, 94], west: [86, 98], japan: [88, 100] },
    touring: { east: [84, 96], west: [88, 104], japan: [92, 108] },
  }[style][region];
  let waist = [...W];
  if (level === "beginner") waist = [Math.max(waist[0] - 6, 66), Math.min(waist[1], 90)];
  if (gender === "kids") waist = [Math.max(waist[0] - 10, 60), Math.max(waist[1] - 10, 70)];
  const pole = Math.round((height * 0.7) / 5) * 5;
  return { len, waist, pole };
}
function readSizer() {
  const v = (id) => $("#" + id).value;
  return { gender: v("sz-g"), height: num(v("sz-h")), weight: num(v("sz-w")), level: v("sz-l"), style: v("sz-s"), region: v("sz-r") };
}
function sizeCalc() {
  const inp = readSizer();
  localStorage.setItem("sd.sizer", JSON.stringify(inp));
  const r = sizeAdvice(inp);
  $("#size-out").innerHTML = r ? `<div class="result-box">双板长度 <b>${r.len[0]}–${r.len[1]} cm</b>，腰宽 <b>${r.waist[0]}–${r.waist[1]} mm</b>，雪杖约 <b>${r.pole} cm</b>
    <div class="note">经验公式：初学约到下巴，中级到鼻子，高级到额头，专家与身高相当；粉雪板更长、刻滑/公园板更短；体重偏大加长一点。雪杖 ≈ 身高 × 0.7。只是起点——最终以试滑和店员建议为准。</div>
    <button class="btn btn-sm" data-act="size-apply">用这个长度和腰宽去筛选双板 →</button></div>` : '<div class="note">请填写身高</div>';
}
function sizeApply() {
  const inp = readSizer(), r = sizeAdvice(inp);
  if (!r) return;
  const f = loadF("ski");
  Object.assign(f, { lenMin: String(r.len[0]), lenMax: String(r.len[1]), waistMin: String(r.waist[0]), waistMax: String(r.waist[1]), types: [] });
  localStorage.setItem("sd.f.ski", JSON.stringify(f));
  S.g.gender = inp.gender === "kids" ? "kids" : inp.gender;
  localStorage.setItem("sd.g", JSON.stringify(S.g));
  location.hash = "#/c/ski";
  toast(`已筛选：${r.len[0]}–${r.len[1]}cm，腰宽 ${r.waist[0]}–${r.waist[1]}mm`);
}
async function saveSettings() {
  const body = { sales_tax_pct: num($("#st-tax").value) ?? 0, notify_desktop: $("#st-notify").checked,
    auto_crawl_hours: num($("#st-auto").value) ?? 24, intl_cache_days: num($("#st-cache").value) ?? 3 };
  S.meta.settings = await api("/api/settings", { method: "PUT", body });
  toast("设置已保存");
}
function renderGuide() {
  const m = new Date().getMonth();  // 0 = 1 月
  const heat = { 0: "", 1: "", 2: "hot", 3: "hot", 4: "warm", 5: "warm", 6: "warm", 7: "warm", 8: "hot", 9: "hot", 10: "hot", 11: "" };
  const sv = loadJSON("sd.sizer", {});
  const opt = (id, pairs, cur) => `<select id="${id}">${pairs.map(([v, t]) => `<option value="${v}" ${cur === v ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  const st = S.meta?.settings || {};
  $("#app").innerHTML = `<div class="page"><div class="guide">
    <div class="section"><h3>🗓 什么时候买最便宜</h3>
      <div class="cal">${Array.from({ length: 12 }, (_, i) => `<div class="${heat[i]} ${i === m ? "now" : ""}">${i + 1}月</div>`).join("")}</div>
      <ul class="plain">
        <li><b>3–4 月（季末清仓）</b>：全年折扣最大，常见 30–60% off；缺点是热门尺码断码。</li>
        <li><b>9–10 月（旧款清仓）</b>：新一季到货，上一季的板、鞋、服装 30–50% off，尺码比春天齐。</li>
        <li><b>11 月底（黑五/网一）</b>：新款也有 15–25%，很多店全场额外折扣。</li>
        <li><b>12–2 月</b>：旺季，折扣少；把想要的商品加入「关注」等降价。</li>
        <li><b>日本</b>：春夏有下一季“早期予約”（预订）折扣，通常 15–30% off。</li>
      </ul></div>
    <div class="section" id="sizer"><h3>📏 尺码助手（双板 & 雪杖）</h3>
      <div class="sizer">
        <label>款式${opt("sz-g", [["men", "男"], ["women", "女"], ["kids", "儿童"]], sv.gender || "men")}</label>
        <label>身高 (cm)<input id="sz-h" inputmode="numeric" value="${esc(sv.height ?? "")}" placeholder="175"></label>
        <label>体重 (kg)<input id="sz-w" inputmode="numeric" value="${esc(sv.weight ?? "")}" placeholder="70"></label>
        <label>水平${opt("sz-l", [["beginner", "初学（绿道）"], ["intermediate", "中级（蓝道）"], ["advanced", "高级（黑道）"], ["expert", "专家（双黑/野雪）"]], sv.level || "intermediate")}</label>
        <label>主要滑法${opt("sz-s", [["all_mountain", "全山（什么都滑）"], ["frontside", "道内刻滑"], ["freeride", "粉雪/野雪"], ["park", "公园/自由式"], ["touring", "登山/野外"], ["race", "竞技"]], sv.style || "all_mountain")}</label>
        <label>常去的雪场${opt("sz-r", [["east", "美东（冰面多）"], ["west", "美西/落基山"], ["japan", "日本（粉雪）"]], sv.region || "east")}</label>
      </div>
      <div style="margin-top:10px"><button class="btn btn-sm" data-act="size-calc">计算</button></div>
      <div id="size-out"></div></div>
    <div class="section"><h3>🧰 装备怎么选（要点）</h3>
      <ul class="plain">
        <li><b>雪鞋</b>：最影响体验的装备，<b>一定要试穿</b>。Mondo 码 ≈ 脚长 cm；硬度 Flex 初学 60–80、进阶 90–110、高级 120+；亚洲人脚常偏宽，优先中楦/宽楦。能找 bootfitter 做热塑内胆更好。</li>
        <li><b>固定器</b>：刹车宽度 ≥ 雪板腰宽；DIN 设定值要落在固定器范围中间；GripWalk 鞋底要配 GW/MNC 固定器；登山要专门的登山固定器。</li>
        <li><b>雪镜</b>：亚洲脸型优先选 <b>亚洲版型（Asian Fit / Low Bridge Fit）</b>，不漏风不起雾；阴天多选高对比/变色镜片，磁吸换片的款式方便换镜片；戴近视眼镜选 OTG。</li>
        <li><b>头盔</b>：带 MIPS/WaveCel 等防旋转结构更安全；和雪镜一起试戴，确保中间没有“缝”（gaper gap）。</li>
        <li><b>雪服/雪裤</b>：分层穿：排汗层（美利奴/化纤，不要棉）→ 中间层（抓绒/薄羽绒）→ 外层。外层防水 ≥ 10K、透气 ≥ 10K 够用；GORE-TEX 最稳。硬壳适合冷热多变/野雪，保暖款（有填充）适合怕冷、美东冰雪。</li>
        <li><b>手套</b>：连指手套（mitten）比五指暖很多；皮革耐磨；怕冷选电加热款。</li>
        <li><b>滑雪袜</b>：只穿一双<b>薄到中厚</b>的专业滑雪袜（美利奴），太厚反而压脚、更冷。</li>
        <li><b>护具</b>：初学者护臀、护腕很值；公园/野雪考虑背部护具。</li>
        <li><b>野外滑雪</b>：信标 + 雪铲 + 探杆是“三件套”，缺一不可；最好上雪崩安全课（AIARE）。</li>
      </ul></div>
    <div class="section"><h3>💰 省钱与避坑</h3>
      <ul class="plain">
        <li><b>先分清“板身”和“含固定器”</b>：套装价看着便宜，但不能和板身直接比；详情页会算“套装 vs 分开买”。</li>
        <li><b>上一季 = 同一件东西换个配色</b>：很多板、鞋、服装连续几年不变，买上一季能省 30% 以上。看详情页的“年份”。</li>
        <li><b>原价虚高</b>：个别店把“原价”标得比别家高，显得折扣大。本工具统一用各店原价的众数/官网价算折扣。</li>
        <li><b>价格匹配</b>：evo、Sports Basement、Aspen Ski and Board、Outdoor Gear Exchange 等接受价格匹配——详情页会提示。</li>
        <li><b>试滑板（Demo）</b>：雪季末雪具店会卖掉试滑板，价格低但有使用痕迹，在筛选里的“成色”勾选即可。</li>
        <li><b>付款</b>：用信用卡或 PayPal，出问题可以争议拒付；不要用转账、Zelle、礼品卡付款给陌生网站。</li>
        <li><b>陌生网站</b>：先到「网站可信度」检测；新注册域名 + 全场 5 折以下 + 只有邮箱 = 高度可疑。</li>
      </ul></div>
    <div class="section"><h3>🚨 雪具网购常见骗局（2024–2026）</h3>
      <ul class="plain">
        <li><b>假“outlet/清仓”站</b>：在 Facebook / Instagram / TikTok 投广告，当季新款标 60–90% off。Burton 官方说当季货超过 6 折就是危险信号。付款后收不到货或收到假货。</li>
        <li><b>仿冒域名</b>：品牌名/店名 + outlet、sale、clearance、-us，常用 .shop/.online/.xyz/.top 后缀。真实案例：<code>utahskigearonline.shop</code> 冒充 Utah Ski Gear。</li>
        <li><b>“幽灵店”</b>：刚注册几周的 Shopify 店，编造“家族老店关门清仓”故事、AI 生成的店主照片、没有电话和地址、退货要寄到海外仓库。</li>
        <li><b>买来的老域名</b>：骗子会买过期的老域名继承“年龄”，所以域名老 ≠ 可信；本工具会检查存档历史是否中断。</li>
      </ul>
      <div class="linkrow"><span class="small muted">参考：</span>
        <a href="https://www.burton.com/us/en/blogs/the-burton-blog/fake-burton-website" target="_blank" rel="noopener noreferrer">Burton 防骗说明</a>
        <a href="https://arcteryx.com/us/en/help/counterfeit" target="_blank" rel="noopener noreferrer">Arc'teryx 仿冒网站</a>
        <a href="https://www.accc.gov.au/media-release/consumers-warned-about-ghost-stores-imitating-australian-businesses" target="_blank" rel="noopener noreferrer">ACCC 幽灵店</a>
        <a href="https://consumer.ftc.gov/consumer-alerts/2025/11/how-avoid-online-shopping-scam-holiday-season" target="_blank" rel="noopener noreferrer">FTC 网购防骗</a>
      </div></div>
    <div class="section"><h3>🌏 在日本 / 中国买</h3>
      <ul class="plain">
        <li><b>日本</b>：价格含 10% 消费税；持护照在实体店消费满 ¥5,000 可免税。东京神保町/御茶之水一带有大量雪具店。雪板托运一般可作为运动器材行李。</li>
        <li><b>中国</b>：同款国行价通常高于美日；注意国内标注的“25/26 款”= 本工具的 2026 款。天猫旗舰店/品牌授权店更有保障，淘宝个人店小心假货和翻新。</li>
        <li><b>汇率</b>：详情页的国际比价按当天欧洲央行汇率换算，信用卡实际汇率会略差 1–3%。</li>
      </ul></div>
    <div class="section"><h3>ℹ️ 数据说明</h3>
      <ul class="plain">
        <li>覆盖 ${S.cats.filter((c) => c.fams).length} 个装备分类；价格来自经核验的美国网站，每天自动更新一次（网页开着时），也可以点右上角“更新数据”。</li>
        <li>REI 通过公开的分类数据抓取；Backcountry、Powder7、Amazon 等有反爬保护，无法自动抓取，详情页提供一键搜索链接。</li>
        <li>同款识别：分类 + 品牌 + 型号（去掉年份/性别/颜色/品类词）+ 性别大类（双板再加“含不含固定器”）。个别标题写法特殊的商品可能分错组。</li>
        <li>工具遵守各网站的 robots.txt，每个网站请求间隔 ≥ 1.5 秒；数据只保存在你自己的电脑上（data/skideals.db）。</li>
      </ul></div>
    <div class="section"><h3>⚙️ 设置</h3>
      <div class="sizer">
        <label>销售税 %（估算到手价）<input id="st-tax" inputmode="decimal" value="${esc(st.sales_tax_pct ?? 6.25)}"></label>
        <label>自动更新间隔（小时，0=关）<input id="st-auto" inputmode="numeric" value="${esc(st.auto_crawl_hours ?? 24)}"></label>
        <label>日本价格缓存（天）<input id="st-cache" inputmode="numeric" value="${esc(st.intl_cache_days ?? 3)}"></label>
        <label style="justify-content:flex-end"><span class="chk"><input type="checkbox" id="st-notify" ${st.notify_desktop !== false ? "checked" : ""}> 降价时弹 Windows 通知</span></label>
      </div>
      <div style="margin-top:10px"><button class="btn btn-sm" data-act="save-settings">保存设置</button></div></div>
  </div></div>`;
  if (sv.height) sizeCalc();
}

// ================================================================ 抓取进度
async function startCrawl(categories = null) {
  try {
    const r = await api("/api/crawl", { method: "POST", body: categories ? { categories } : {} });
    if (!r.started) toast("已经在更新中");
    else if (categories) toast(`开始更新：${categories.map(catName).join("、")}`);
    $("#btn-crawl").disabled = true;
    delete $("#crawl-pop").dataset.dismissed;
    pollCrawl();
  } catch (e) { toast("启动失败：" + e.message); }
}
async function pollCrawl() {
  let st;
  try { st = await api("/api/crawl/status"); } catch (e) { return setTimeout(pollCrawl, 4000); }
  const pop = $("#crawl-pop");
  const icon = { queued: "…", running: "⏳", ok: "✓", partial: "◐", blocked: "⚠", robots: "⚠", error: "✗" };
  const rs = Object.entries(st.retailers || {});
  const done = rs.filter(([, r]) => !["queued", "running"].includes(r.status)).length;
  pop.innerHTML = `<b>${st.running ? `正在更新数据（${done}/${rs.length}）` : "更新完成"}</b>
    <div class="note" style="margin:2px 0 6px">每个网站间隔 1.5 秒请求，全品类约需 20–30 分钟（大站 evo 最慢），可以关掉这个框继续浏览。</div>
    ${rs.map(([id, r]) => `<div class="row"><span>${icon[r.status] || ""} ${esc(r.name || id)}</span><span class="muted">${r.n || 0}${r.status === "running" && r.msg ? " · " + esc(r.msg.replace("正在抓：", "")) : ""}${r.status === "error" || r.status === "blocked" ? " · " + esc((r.msg || "").slice(0, 24)) : ""}</span></div>`).join("")}
    <div style="text-align:right;margin-top:6px"><button class="link-btn small" onclick="const p=this.closest('.crawl-pop');p.dataset.dismissed='1';p.classList.add('hidden')">关闭</button></div>`;
  if (st.running) {
    if (!pop.dataset.dismissed) pop.classList.remove("hidden");
    setTimeout(pollCrawl, 2500);
  } else {
    $("#btn-crawl").disabled = false;
    S.data = {};  // 数据变了，清空分类缓存
    await reloadAll();
    toast("数据已更新 ✓");
    setTimeout(() => pop.classList.add("hidden"), 5000);
  }
}

// ================================================================ 路由 & 启动
async function loadMeta() {
  S.meta = await api("/api/meta");
  $("#last-crawl").textContent = S.meta.last_crawl ? "更新于 " + ago(S.meta.last_crawl) : "尚未抓取";
  const b = $("#watch-badge");
  b.textContent = S.meta.unseen_alerts || "";
  b.classList.toggle("hidden", !S.meta.unseen_alerts);
  return S.meta;
}
async function loadCategories() {
  const r = await api("/api/categories");
  S.cats = r.categories;
  S.catInfo = Object.fromEntries(r.categories.map((c) => [c.id, c]));
  S.feat = r.features;
  S.sizeOrder = r.size_order;
}
async function reloadAll() {
  await Promise.all([loadMeta(), loadCategories()]);
  route();
}
function route() {
  if (!$("#drawer").classList.contains("hidden")) closeDrawer();  // 切换页面时关掉详情抽屉
  const h = location.hash.replace(/^#\/?/, "");
  const m = h.match(/^c\/([a-z]+)/);
  if (m && S.catInfo[m[1]]) { S.catId = m[1]; localStorage.setItem("sd.cat", S.catId); }
  if (!S.catInfo[S.catId]) S.catId = "ski";
  S.route = ["watch", "trust", "guide"].includes(h) ? h : "deals";
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === S.route));
  if (S.route === "deals") renderDeals();
  else if (S.route === "watch") renderWatch();
  else if (S.route === "trust") renderTrust();
  else renderGuide();
  window.scrollTo(0, 0);
}
async function init() {
  bindDeals(); bindDrawer(); bindTrust();
  $("#btn-crawl").addEventListener("click", () => startCrawl());
  const onSearch = debounce(() => {
    S.q = $("#q").value.trim().toLowerCase();
    if (S.route !== "deals") location.hash = "#/c/" + S.catId;
    else refresh();
  }, 200);
  $("#q").addEventListener("input", onSearch);
  window.addEventListener("hashchange", route);
  try {
    await reloadAll();
    if (S.meta.crawl_running) { $("#btn-crawl").disabled = true; pollCrawl(); }
  } catch (e) {
    $("#app").innerHTML = `<div class="empty">无法连接本地服务：${esc(e.message)}<br>请确认 start.bat 的窗口还开着。</div>`;
  }
  setInterval(() => loadMeta().catch(() => {}), 60000);
}
init();
