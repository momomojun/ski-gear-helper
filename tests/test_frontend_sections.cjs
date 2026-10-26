const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').replace(/init\(\);\s*$/, '');

function page() {
  const values = new Map();
  const context = vm.createContext({ localStorage: { getItem: (k) => values.get(k) ?? null,
    setItem: (k,v) => values.set(k,v) } });
  vm.runInContext(source, context);
  vm.runInContext(`S.catInfo=Object.fromEntries(DEFAULT_SECTIONS.flatMap(s=>s.categories).map(id=>[id,{id}]));
    S.cats=Object.values(S.catInfo);`, context);
  return (js) => vm.runInContext(js, context);
}

test('home always opens ski first, including after browsing outdoor shoes', () => {
  const run = page();
  run("S.section='outdoor'; S.catId='hiking_shoe'; localStorage.setItem('sd.cat','hiking_shoe'); localStorage.setItem('sd.cat.outdoor','trail_shoe')");
  assert.equal(run("resolveDealPath('').section"), 'ski');
  assert.equal(run("resolveDealPath('').cat"), 'ski');
  assert.equal(run("resolveDealPath('outdoor').cat"), 'trail_shoe');
});

test('category links cannot leak ski hardgoods into outdoor or shoes into ski', () => {
  const run = page();
  assert.equal(run("resolveDealPath('outdoor/c/ski').cat"), 'hiking_shoe');
  assert.equal(run("resolveDealPath('c/hiking_shoe').cat"), 'ski');
  assert.equal(run("resolveDealPath('outdoor/c/outdoor_jacket').cat"), 'outdoor_jacket');
  assert.equal(run("sectionCats('ski').some(c=>c.id==='trail_shoe')"), false);
  assert.equal(run("sectionCats('outdoor').some(c=>c.id==='binding')"), false);
  assert.equal(run("sectionCats('ski').some(c=>c.id==='midlayer') && sectionCats('outdoor').some(c=>c.id==='midlayer')"), true);
});

test('shared garments keep independent section filters and correctly scoped links', () => {
  const run = page();
  run("S.section='ski'; S.catId='midlayer'; S.f=defaultF('midlayer'); S.f.sizes=['M']; saveF()");
  run("S.section='outdoor'; S.f=loadF('midlayer')");
  assert.equal(run('S.f.sizes.length'), 0);
  run("S.f.sizes=['XL']; saveF(); S.section='ski'");
  assert.equal(run("loadF('midlayer').sizes[0]"), 'M');
  assert.equal(run("categoryPath('midlayer','outdoor')"), '#/outdoor/c/midlayer');
});

test('shoe size is exact, never Mondo shell matching or apparel slash splitting', () => {
  const run = page();
  assert.equal(run("sizeMatch('hiking_shoe',{sizes:['US Men 9 / D']},['US Men 9 / D'])"), true);
  assert.equal(run("sizeMatch('hiking_shoe',{sizes:['US Men 9 / D']},['US Women 9 / B'])"), false);
  assert.equal(run("sizeMatch('trail_shoe',{sizes:['US Men 9 / D']},['US Men 9.5 / D'])"), false);
  assert.equal(run("sizeMatch('trail_shoe',{sizes:['US Men 9 / D']},['EU 42 / Regular'])"), false);
});

test('shoe labels are readable while preserving original size-system and width evidence', () => {
  const run = page();
  assert.equal(run("sizeLabel('hiking_shoe','US:men:9.5:wide')"), 'US 男码 9.5 · 宽楦');
  assert.equal(run("sizeLabel('trail_shoe','未注明:women:8:b')"), '码制未注明 女码 8 · B');
  assert.equal(run("sizeLabel('midlayer','M/L')"), 'M/L');
});

test('brand facets sort A-Z regardless of counts, with selected zero-count brands retained', () => {
  const run = page();
  const rows = JSON.parse(run(`JSON.stringify(sortBrandOptions({'Patagonia':999,'Arc’teryx':1,'Salomon':5,'Altra':2,'':20},['Black Diamond']))`));
  assert.deepEqual(rows.map(r=>r[0]),['Altra','Arc’teryx','Black Diamond','Patagonia','Salomon','']);
  assert.deepEqual(rows[2],['Black Diamond',0]);
});

test('shoe chips combine colours using the cheapest in-stock SKU without mixing widths', () => {
  const run = page();
  run(`S.catId='trail_shoe';S.f=defaultF('trail_shoe');const offer={sizes:[
    {n:'US:men:9:d',a:false,p:40,u:'/sold'},
    {n:'US:men:9:d',a:true,p:100,u:'/blue'},
    {n:'US:men:9:d',a:true,p:80,u:'/green'},
    {n:'US:men:9:2e',a:true,p:110,u:'/wide'}]}`);
  const html = run("sizeChips(offer,'trail_shoe')");
  assert.equal((html.match(/class="sz /g)||[]).length,2);
  assert(html.includes('/green') && html.includes('/wide'));
  assert(!html.includes('/sold') && !html.includes('/blue'));
});
