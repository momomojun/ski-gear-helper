const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').replace(/init\(\);\s*$/, '');

test('category badges distinguish partial, empty, blocked and complete outcomes', () => {
  const ctx = vm.createContext({ localStorage: { getItem: () => null }, ic: () => '', catIc: () => '', CAT_SHORT: { ski: '双板' } });
  vm.runInContext(source, ctx);
  for (const [status, label] of [['partial', '部分覆盖'], ['empty', '零结果待核查'], ['blocked', '被反爬拦截']]) {
    const html = vm.runInContext(`catsHTML({cats:{ski:{status:'${status}',n:1,msg:'example'}}})`, ctx);
    assert.ok(html.includes(label));
    assert.ok(!html.includes(' · 失败'));
  }
  const success = vm.runInContext("catsHTML({cats:{ski:{status:'ok',n:1,msg:'done'}}})", ctx);
  assert.ok(!success.includes('drop'));
});
