"""Corbetts 的公开 Searchspring 分类目录；币种及分类范围取自商家页面。"""
import html
import json
import re
from collections import defaultdict
from urllib.parse import urljoin, urlencode

from selectolax.lexbor import LexborHTMLParser

from .base import Adapter, RawProduct, SizeOption, parse_cm, to_float


def parse_product(p, category, currency):
    title = html.unescape(p.get('name', '')).strip()
    if not title or not p.get('url') or not p.get('uid'):
        return None
    url = urljoin('https://www.corbetts.com', p['url'])
    variants = defaultdict(list)
    options = p.get('child_sku_options') or '[]'
    if isinstance(options, str):
        options = json.loads(html.unescape(options))
    for opt in options:
        variants[str(opt['id'])].append(opt)
    has_ski_only = any(o.get('option') == 'Select a Binding' and o.get('value') == 'Ski Only' for o in options)
    sizes = []
    for group in variants.values():
        if category == 'ski' and has_ski_only and any(o.get('option') == 'Select a Binding' and o.get('value') != 'Ski Only' for o in group):
            continue  # 不把选配固定器套装价格混入裸板报价。
        size = next((str(o['value']) for o in group if re.search(r'size|length|width', o.get('option', ''), re.I)), None)
        o = group[0]
        price = to_float(o.get('calculated_price', o.get('price')))
        if not size or price is None or 'inventory_level' not in o:
            continue
        available = float(o['inventory_level'] or 0) > 0 and o.get('purchasing_disabled') not in (True, 'true')
        params = {f"attribute[{v['product_option_id']}]": v['option_value_id'] for v in group}
        sizes.append(SizeOption(size, cm=parse_cm(size) if category in ('ski', 'pole') else None,
                                price=price, compare_at=to_float(o.get('retail_price')), available=available,
                                url=url + '?' + urlencode(params),
                                color=next((str(v['value']) for v in group if 'color' in v.get('option', '').lower()), None)))
    live = [s for s in sizes if s.available]
    cheapest = min(live or sizes, key=lambda s: s.price) if sizes else None
    price = cheapest.price if cheapest else to_float(p.get('price'))
    if price is None:
        return None
    msrp = cheapest.compare_at if cheapest else to_float(p.get('msrp'))
    return RawProduct(retailer_id='corbetts', external_id=str(p['uid']), title=title, url=url,
                      price=price, compare_at=msrp if msrp and msrp > price else None, currency=currency,
                      vendor=p.get('brand'), image=p.get('ss_image') or p.get('imageUrl'), sizes=sizes,
                      available=bool(live) if sizes else p.get('ss_in_stock') == '1', category=category,
                      body_text=html.unescape(p.get('ss_snippet', '')), product_type=p.get('product_type_unigram'))


class CorbettsAdapter(Adapter):
    def fetch(self):
        seen = set()
        for path in self.cfg.get('pages', []):
            try:
                page_url = urljoin(self.base_url, path)
                source = self.fetcher.get_html(page_url)
                match = re.search(r'category\s*=\s*\{[^}]*path\s*:\s*"([^"]+)"', source)
                site = re.search(r'snapui\.searchspring\.io/([a-z0-9]{6})/bundle\.js', source)
                tree = LexborHTMLParser(source)
                currency_node = tree.css_first('[data-currency-code]')
                if not match or not site or currency_node is None:
                    raise ValueError('Corbetts 分类上下文/币种缺失')
                currency = currency_node.attributes['data-currency-code']
                if currency != self.cfg.get('currency', 'CAD'):
                    raise ValueError(f'页面币种 {currency} 与配置不一致')
                hierarchy = '>'.join(x.strip() for x in html.unescape(match[1]).split('>'))
                endpoint = f'https://{site[1]}.a.searchspring.io/api/search/category.json'
                max_pages = int(self.cfg.get('max_pages', 15))
                expected_total = None
                fetched_ids = set()
                for page in range(1, max_pages + 1):
                    data = self.fetcher.get_json(endpoint, params={'siteId': site[1], 'resultsFormat': 'json',
                        'bgfilter.categories_hierarchy': hierarchy, 'resultsPerPage': 48, 'page': page})
                    pagination = data['pagination']
                    total = int(pagination['totalResults'])
                    if expected_total is not None and expected_total != total:
                        self.mark_incomplete('抓取期间目录总数变化')
                    expected_total = total
                    batch = data['results']
                    if not batch and total:
                        raise ValueError('目录声明有商品但返回空页')
                    for p in batch:
                        pid = str(p.get('uid', ''))
                        fetched_ids.add(pid)
                        if pid in seen:
                            continue
                        raw = parse_product(p, self.category, currency)
                        if raw is None:
                            self.mark_incomplete('部分商品缺少有效报价或标识')
                        elif self.wanted(raw.title):
                            seen.add(pid)
                            yield raw
                    if page >= int(pagination['totalPages']):
                        if len(fetched_ids) != total:
                            self.mark_incomplete('去重后的商品数与目录总数不一致')
                        break
                else:
                    self.mark_incomplete(f'达到目录分页预算 {max_pages}')
            except Exception as e:
                self.record_fetch_error(e)
