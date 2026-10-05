"use strict";
/* 图标：统一的 24×24 线条图标（描边 1.6，圆角端点）。界面图标 + 22 个装备分类图标，全部手绘，不依赖外部资源。
 * 用法：ic("search")、catIc("ski")；返回 <svg> 字符串。 */

const ICONS = {
  // ---------- 界面 ----------
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.6L4 8"/><path d="M4 3.5V8h4.5"/><path d="M4 13a8 8 0 0 0 14.3 4.6L20 16"/><path d="M20 20.5V16h-4.5"/>',
  sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a6.8 6.8 0 0 0 10.7 10.7z"/>',
  auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor" stroke="none"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8l-5.4 2.9 1.1-6.1-4.5-4.2 6.1-.8z"/>',
  starFill: '<path d="m12 3 2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 16.8l-5.4 2.9 1.1-6.1-4.5-4.2 6.1-.8z" fill="currentColor"/>',
  bell: '<path d="M18 9.5a6 6 0 0 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15.5 18 9.5z"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/>',
  shield: '<path d="M12 21s7.5-3.4 7.5-9.6V5.6L12 3 4.5 5.6v5.8C4.5 17.6 12 21 12 21z"/><path d="m8.8 12 2.2 2.2 4.4-4.5"/>',
  book: '<path d="M3 4.5h5.5A3.5 3.5 0 0 1 12 8v12.5a2.6 2.6 0 0 0-2.6-2.6H3z"/><path d="M21 4.5h-5.5A3.5 3.5 0 0 0 12 8v12.5a2.6 2.6 0 0 1 2.6-2.6H21z"/>',
  tag: '<path d="M3.5 12.8V4.5a1 1 0 0 1 1-1h8.3l7.7 7.7a1.6 1.6 0 0 1 0 2.3l-6 6a1.6 1.6 0 0 1-2.3 0z"/><circle cx="8.3" cy="8.3" r="1.3"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  check: '<path d="m4.5 12.5 4.5 4.5L19.5 6.5"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronLeft: '<path d="m15 6-6 6 6 6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  arrowRight: '<path d="M4 12h15.5"/><path d="m13.5 6 6 6-6 6"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.2M12 7.8v.1"/>',
  alert: '<path d="M10.3 4.2 2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4.2M12 16.9v.1"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  ruler: '<path d="M3.6 15.8 15.8 3.6a1.4 1.4 0 0 1 2 0l2.6 2.6a1.4 1.4 0 0 1 0 2L8.2 20.4a1.4 1.4 0 0 1-2 0l-2.6-2.6a1.4 1.4 0 0 1 0-2z"/><path d="m7.5 12 1.8 1.8M10.5 9l1.8 1.8M13.5 6l1.8 1.8"/>',
  toolbox: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M8.5 8V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2M3 13h18M10 13v2.5h4V13"/>',
  coins: '<ellipse cx="9" cy="7" rx="5.5" ry="2.5"/><path d="M3.5 7v4c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5V7"/><path d="M9.5 16.4c.9.1 1.4.1 1.5.1M3.5 11v4c0 1.4 2.5 2.5 5.5 2.5"/><ellipse cx="15.5" cy="14" rx="5" ry="2.3"/><path d="M10.5 14v3.7c0 1.3 2.2 2.3 5 2.3s5-1 5-2.3V14"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.4 5.3 3.4 8.5s-1.1 6.1-3.4 8.5c-2.3-2.4-3.4-5.3-3.4-8.5S9.7 5.9 12 3.5z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M2.8 12h2.4M18.8 12h2.4M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.8"/>',
  zap: '<path d="M13 2.5 4.5 13.5H12L11 21.5l8.5-11H12z"/>',
  trendDown: '<path d="m3 6.5 6.5 6.5 4-4L21 16.5"/><path d="M21 11v5.5h-5.5"/>',
  trophy: '<path d="M7.5 4h9v5a4.5 4.5 0 0 1-9 0z"/><path d="M16.5 5.5H20c0 3-1.6 4.8-3.8 5M7.5 5.5H4c0 3 1.6 4.8 3.8 5M12 13.5V17M8.5 20.5h7M9.5 17h5v3.5h-5z"/>',
  pin: '<path d="M19.5 10c0 5.6-7.5 11-7.5 11s-7.5-5.4-7.5-11a7.5 7.5 0 0 1 15 0z"/><circle cx="12" cy="10" r="2.6"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  store: '<path d="M4 9.5V20h16V9.5"/><path d="M3 9.5 4.8 4h14.4L21 9.5a2.8 2.8 0 0 1-5.4.6 2.8 2.8 0 0 1-3.6 1.7 2.8 2.8 0 0 1-3.6-1.7A2.8 2.8 0 0 1 3 9.5z"/><path d="M9.5 20v-5h5v5"/>',
  sparkle: '<path d="M12 3.5c.6 3.9 2.6 5.9 6.5 6.5-3.9.6-5.9 2.6-6.5 6.5-.6-3.9-2.6-5.9-6.5-6.5 3.9-.6 5.9-2.6 6.5-6.5z"/><path d="M18.5 15.5c.3 1.6 1 2.3 2.5 2.5-1.5.2-2.2.9-2.5 2.5-.3-1.6-1-2.3-2.5-2.5 1.5-.2 2.2-.9 2.5-2.5z"/>',
  bulb: '<path d="M9 17.5h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.3v1.4h5v-1.4c0-.9.4-1.7 1.1-2.3A6 6 0 0 0 12 3z"/>',
  filter: '<path d="M3.5 5h17l-6.5 7.8V19l-4 1.8v-8z"/>',
  github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
  heartbeat: '<path d="M3 12h4l2.5-5 4 10 2.5-5h5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2.2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  mountain: '<path d="M2.5 19.5 9 8l3.6 6.2L15 10.5l6.5 9z"/><path d="m7.4 10.8 1.6 1.2 1.5-1.2"/>',
  snow: '<path d="M12 2.8v18.4M4 7.4l16 9.2M4 16.6l16-9.2"/><path d="m9.6 4.2 2.4 2 2.4-2M9.6 19.8l2.4-2 2.4 2M3.6 10.3l3.1-.5-1-3M20.4 13.7l-3.1.5 1 3M3.6 13.7l3.1.5-1 3M20.4 10.3l-3.1-.5 1-3"/>',
  // 雪道难度标志（北美）：绿圆 / 蓝方 / 黑钻 / 双黑钻
  pisteGreen: '<circle cx="12" cy="12" r="6.5" fill="currentColor" stroke="none"/>',
  pisteBlue: '<rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor" stroke="none"/>',
  pisteBlack: '<path d="M12 4.5 19.5 12 12 19.5 4.5 12z" fill="currentColor" stroke="none"/>',
  pisteDouble: '<path d="M7.5 6 13 12l-5.5 6L2 12zM16.5 6 22 12l-5.5 6L11 12z" fill="currentColor" stroke="none"/>',
};

// ---------- 22 个装备分类 ----------
const CAT_ICONS = {
  ski: '<path d="M3 18.5 18.6 10c1.6-.9 2-2.8.7-3.6"/><path d="M5.4 21.8 21 13.3c1.6-.9 2-2.8.7-3.6"/><path d="m8.6 14.4 2.6-1.4M11 17.7l2.6-1.4"/>',
  binding: '<path d="M2.5 17.5h19"/><path d="M4 17.5v-3.2A1.8 1.8 0 0 1 5.8 12.5H9l.8 5"/><path d="M14.5 17.5l1-6.5h3.2a1.8 1.8 0 0 1 1.8 1.8v4.7"/><path d="M17.5 17.5l2 3.5M10 15h4"/>',
  boot: '<path d="M7.5 3h6l.8 5.6 4.6 3.2a3.5 3.5 0 0 1 1.6 2.9V18h-16l1.3-9.2z"/><path d="M3.5 18h17v2.6h-17zM8.7 7.5h4.2M8.4 11h5.2M9 14.5h7"/>',
  pole: '<path d="M8 4v17M16 4v17"/><path d="M6.6 2.5h2.8v5.2H6.6zM14.6 2.5h2.8v5.2h-2.8z"/><path d="M5.3 17.5h5.4M13.3 17.5h5.4"/>',
  helmet: '<path d="M3.5 16a8.5 8.5 0 0 1 17 0v1.8a.7.7 0 0 1-.7.7H4.2a.7.7 0 0 1-.7-.7z"/><path d="M12 7.5v3.2M8 8.6l1.2 2.6M16 8.6l-1.2 2.6"/><path d="M3.6 14.2h16.8"/>',
  goggle: '<path d="M3 10.6A3.1 3.1 0 0 1 6.1 7.5h11.8a3.1 3.1 0 0 1 3.1 3.1v1.8a3.6 3.6 0 0 1-3.6 3.6h-2.3a2.6 2.6 0 0 1-2.2-1.2l-.4-.6a.6.6 0 0 0-1 0l-.4.6a2.6 2.6 0 0 1-2.2 1.2H6.6A3.6 3.6 0 0 1 3 12.4z"/><path d="M1.3 11.5H3M21 11.5h1.7"/><path d="M6.4 10.4l2.4-.1" opacity=".55"/>',
  protection: '<path d="M8.2 3c0 1.6 1.7 2.8 3.8 2.8s3.8-1.2 3.8-2.8l2.7 1.9V20a1.2 1.2 0 0 1-1.2 1.2H6.7A1.2 1.2 0 0 1 5.5 20V4.9z"/><path d="M5.5 10.2h13M5.5 15.4h13M12 5.8v15.4"/>',
  jacket: '<path d="M8.4 3.6 5 5 2.6 11.4l2.6 1.1 1-2.3V21h11.6V10.2l1 2.3 2.6-1.1L19 5l-3.4-1.4"/><path d="M8.4 3.6c0 2.1 1.6 3.6 3.6 3.6s3.6-1.5 3.6-3.6"/><path d="M12 7.2V21M8.4 14h1.6M14 14h1.6"/>',
  pants: '<path d="M6 3.2h12l1.6 17.6h-5.2L12 10.4l-2.4 10.4H4.4z"/><path d="M6 6.6h12M12 6.6v3.8"/>',
  suit: '<path d="M8.6 3 5.2 4.4 3 10.2l2.2.9 1.4-2.6V13l-1.2 8h4.4l2.2-6.2 2.2 6.2h4.4l-1.2-8V8.5l1.4 2.6 2.2-.9-2.2-5.8L15.4 3"/><path d="M8.6 3c0 1.9 1.5 3.2 3.4 3.2S15.4 4.9 15.4 3M12 6.2v8.6"/>',
  midlayer: '<path d="M8.8 3.8 5 5.2 2.6 11.4l2.6 1.1 1-2.3V21h11.6V10.2l1 2.3 2.6-1.1L19 5.2l-3.8-1.4"/><path d="m8.8 3.8 3.2 2.4 3.2-2.4M12 6.2v5"/><path d="M8 15.5c1.3.7 2.7.7 4 0s2.7-.7 4 0" opacity=".6"/>',
  baselayer: '<path d="M9.2 3.6 4.8 5.4 2.6 14l2.6.6 1.5-5.4V21h10.6V9.2l1.5 5.4 2.6-.6-2.2-8.6-4.4-1.8"/><path d="M9.2 3.6a2.8 2.8 0 0 0 5.6 0"/>',
  glove: '<path d="M8.6 15.2V6.8a3.4 3.4 0 0 1 6.8 0v8.4"/><path d="M8.6 12.4 6.9 10.7a1.6 1.6 0 0 0-2.3 2.3l4 4"/><path d="M8 15.2h8V21H8z"/>',
  sock: '<path d="M9.2 3h6v9.4l-4.8 7a2.7 2.7 0 0 1-4.4-3.1L9.2 12z"/><path d="M9.2 6.4h6M7.6 17.8l2.2 1.6" opacity=".6"/>',
  facewear: '<path d="M6 5.4C6 4 8.7 3 12 3s6 1 6 2.4v13.2C18 20 15.3 21 12 21s-6-1-6-2.4z"/><path d="M6 5.4c0 1.4 2.7 2.4 6 2.4s6-1 6-2.4"/><path d="M6 12.4c2 .9 4 .9 6 0s4-.9 6 0M6 16.2c2 .9 4 .9 6 0s4-.9 6 0" opacity=".6"/>',
  hat: '<circle cx="12" cy="4.4" r="1.9"/><path d="M5 15a7 7 0 0 1 14 0"/><rect x="4" y="15" width="16" height="4.6" rx="1.4"/><path d="M8 15v4.6M12 15v4.6M16 15v4.6" opacity=".55"/>',
  backpack: '<path d="M6.8 9.5A5.2 5.2 0 0 1 17.2 9.5V19a2 2 0 0 1-2 2H8.8a2 2 0 0 1-2-2z"/><path d="M10 4.6V3.4h4v1.2"/><path d="M9.4 14.4h5.2V18H9.4zM6.8 11.6h10.4"/>',
  bag: '<path d="M3.5 11a3.5 3.5 0 0 1 3.5-3.5h10a3.5 3.5 0 0 1 3.5 3.5v7.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M9 7.5V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1.5M7.2 7.6V20M16.8 7.6V20"/>',
  avalanche: '<rect x="6.5" y="8" width="11" height="13" rx="2.4"/><path d="M8.8 11h6.4v3.6H8.8zM9.6 17.8h1.2M13.2 17.8h1.2"/><path d="M9 5.2a4.4 4.4 0 0 1 6 0M10.6 3a2 2 0 0 1 2.8 0" opacity=".8"/>',
  skin: '<path d="M7.8 3h8.4L15 21H9z"/><path d="m9.8 7.4 2.2 1.8 2.2-1.8M10 11.6l2 1.7 2-1.7M10.2 15.8l1.8 1.5 1.8-1.5"/>',
  tuning: '<path d="M4 15.5c0-3.3 2.6-6 6.6-6h7.9A1.5 1.5 0 0 1 20 11v4.5z"/><path d="M3 15.5h18v2.6H3zM11 9.5V7a1.5 1.5 0 0 1 1.5-1.5h5.2"/><path d="M7 20.8v.4M11.5 20.8v.4M16 20.8v.4" opacity=".6"/>',
  accessory: '<rect x="3.8" y="3.8" width="7" height="7" rx="2"/><rect x="13.2" y="3.8" width="7" height="7" rx="2"/><rect x="3.8" y="13.2" width="7" height="7" rx="2"/><path d="M16.7 13.6v6.2M13.6 16.7h6.2"/>',
};

// 分类栏用的短名（4 个字以内）
const CAT_SHORT = {
  ski: "双板", binding: "固定器", boot: "雪鞋", pole: "雪杖", helmet: "头盔", goggle: "雪镜", protection: "护具",
  jacket: "雪服", pants: "雪裤", suit: "连体服", midlayer: "中间层", baselayer: "保暖内衣", glove: "手套", sock: "滑雪袜",
  facewear: "护脸围脖", hat: "帽子", backpack: "滑雪背包", bag: "雪具包", avalanche: "雪崩装备", skin: "止滑带",
  tuning: "打蜡修板", accessory: "其他配件",
};

function svgIcon(body, size, cls) {
  return `<svg class="ic${cls ? " " + cls : ""}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}
const ic = (name, size = 18, cls = "") => svgIcon(ICONS[name] || ICONS.info, size, cls);
const catIc = (id, size = 22, cls = "") => svgIcon(CAT_ICONS[id] || CAT_ICONS.accessory, size, cls);

// 品牌标志：晴天（bluebird day）里的雪山
function logoMark(size = 32, id = "lm-x") {
  return `<svg class="logo-mark" width="${size}" height="${size}" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4C8DFF"/><stop offset="1" stop-color="#1446D8"/></linearGradient></defs>
    <rect width="32" height="32" rx="9" fill="url(#${id})"/>
    <circle cx="23.2" cy="9.4" r="2.7" fill="#FFD27A"/>
    <path d="M4.5 25 12.4 12l4 6.3 2.9-4.1L27.5 25z" fill="#fff"/>
    <path d="M12.4 12 14.9 16l-1.6-.9-1.1 1.1-1.3-1.2-1.3.4z" fill="#BFD4FF"/>
  </svg>`;
}
