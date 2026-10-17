// Regression: a cheap small colour must not become the XL purchase price/URL.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8')
  .replace(/init\(\);\s*$/, '');

function page() {
  const context = vm.createContext({ localStorage: { getItem: () => null } });
  vm.runInContext(source, context);
  vm.runInContext(`
    S.catId = 'midlayer'; S.f = defaultF('midlayer');
    const d = {category:'midlayer', msrp:{None:145}, offers:[
      {id:1,r:'scheels',p:97.30,a:1,cd:'new',u:'/small',lo:1,pp:145,
       z:[['S',1,97.30,'/small','Green'],['M',1,139,'/medium','Blue'],['XL',1,100.99,'/xl','Blue Sag'],['XXL',0,80,'/sold','Black']],
       sizes:[{n:'S',p:97.30,u:'/small'},{n:'M',p:139,u:'/medium'},{n:'XL',p:100.99,u:'/xl'}]},
      {id:2,r:'other',p:145,a:1,cd:'new',u:'/other',z:[['XL',1,145],['M',1,145]]}
    ]};
  `, context);
  return (js) => vm.runInContext(js, context);
}

test('XL card, drawer and purchase link use the XL sale colour', () => {
  const run = page(); run("S.f.sizes=['XL']");
  assert.equal(run("offerPass(d.offers[0],S.f,null,'midlayer').p"), 100.99);
  assert.equal(run('bestOffer(d).best.p'), 100.99);
  assert.equal(run('bestOffer(d).best.u'), '/xl');
  assert.equal(run('bestOffer(d).best.color'), 'Blue Sag');
  assert.equal(run('detailPriceScope(d).offers[0].p'), 100.99);
  assert.equal(run('bestOffer(d).best.lo'), null);
});

test('medium does not inherit the cheap small price; no size keeps overall starting price', () => {
  const run = page(); run("S.f.sizes=['M']");
  assert.equal(run('bestOffer(d).best.p'), 139);
  assert.equal(run('bestOffer(d).best.u'), '/medium');
  run('S.f.sizes=[]');
  assert.equal(run('bestOffer(d).best.p'), 97.30);
});

test('sold out selected size is explicitly marked missing, not offered at its price', () => {
  const run = page(); run("S.f.sizes=['XXL']");
  assert.equal(run('bestOffer(d).missing'), true);
  assert.notEqual(run('bestOffer(d).best.p'), 80);
});

test('unknown M price cannot borrow the S starting price', () => {
  const run = page();
  run("d.offers[0].z[1][2]=null; d.offers=d.offers.slice(0,1); S.f.sizes=['M']");
  assert.equal(run("offerPass(d.offers[0],S.f,null,'midlayer')"), null);
  assert.equal(run('bestOffer(d).missing'), true);
});

test('allowing sold out quotes keeps the selected variant unavailable', () => {
  const run = page(); run("S.f.sizes=['XXL']; S.f.inStock=false");
  assert.equal(run('bestOffer(d).best.p'), 80);
  assert.equal(run('bestOffer(d).best.a'), 0);
  assert.equal(run('bestOffer(d).isNew'), false);
});

test('an older infant MSRP is not inflated by another toddler offer', () => {
  const run = page();
  assert.equal(run("offerMsrp({msrp:{None:99}}, {y:null,c:89})"), 89);
  assert.equal(run("offerMsrp({msrp:{None:145}}, {y:null,c:null})"), 145);
});
