"""Bob's Sports Chalet 的公开分类卡片；只读允许的分类入口，不请求 robots 禁止的 browse 分页。"""
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from .base import Adapter, RawProduct, to_float
from .schema_org import extract_json_ld, is_product_type


def parse_cards(source, base_url, category):
    tree = LexborHTMLParser(source)
    for card in tree.css('.ps-shoe'):
        a = card.css_first('a.ps-shoe__name[href]')
        price_node = card.css_first('.ps-shoe__price')
        if a is None or price_node is None:
            continue
        url = urljoin(base_url, a.attributes['href'])
        old_node = price_node.css_first('del, s')
        old = to_float(old_node.text().replace('$', '').strip()) if old_node else None
        if old_node:
            old_node.decompose()
        values = re.findall(r'\$\s*([\d,]+\.\d{2})', price_node.text())
        price = to_float(values[-1]) if len(values) == 1 else None
        if price is None:
            continue
        image = card.css_first('img')
        yield RawProduct(retailer_id='bobssportschalet', external_id=url.rstrip('/').rsplit('-', 1)[-1],
                         title=a.text(strip=True), url=url, price=price,
                         compare_at=old if old and old > price else None, currency='USD',
                         image=urljoin(base_url, image.attributes.get('src', '')) if image else None,
                         available=None, category=category)


class BobsAdapter(Adapter):
    def fetch(self):
        self.mark_incomplete('仅覆盖允许的分类入口；browse 分页被 robots 禁止')
        seen = set()
        remaining = int(self.cfg.get('max_detail_pages', 12))
        for path in self.cfg.get('pages', []):
            try:
                source = self.fetcher.get_html(urljoin(self.base_url, path))
                products = [p for p in parse_cards(source, self.base_url, self.category) if self.wanted(p.title)]
                for raw in products:
                    if raw.external_id in seen:
                        continue
                    seen.add(raw.external_id)
                    if remaining <= 0:
                        self.mark_incomplete('达到详情预算，剩余商品库存未知')
                        yield raw
                        continue
                    remaining -= 1
                    try:
                        detail = self.fetcher.get_html(raw.url)
                        for obj in extract_json_ld(detail):
                            if not is_product_type(obj.get('@type')):
                                continue
                            offers = obj.get('offers')
                            offers = [offers] if isinstance(offers, dict) else offers or []
                            verified = [o for o in offers if o.get('@type') == 'Offer' and o.get('priceCurrency') == 'USD'
                                        and o.get('availability') and to_float(o.get('price'))]
                            live = [o for o in verified if o['availability'].rsplit('/', 1)[-1] in ('InStock', 'LimitedAvailability')]
                            if verified:
                                raw.price = min(to_float(o['price']) for o in live or verified)
                                raw.available = bool(live)
                                brand = obj.get('brand')
                                raw.vendor = brand.get('name') if isinstance(brand, dict) else brand
                                raw.body_text = obj.get('description')
                                break
                    except Exception as e:
                        self.record_fetch_error(e)
                    yield raw
            except Exception as e:
                self.record_fetch_error(e)
