"""命令行入口。

  python -m skideals serve [--open]        启动网页（默认 http://127.0.0.1:8765）
  python -m skideals crawl [id ...]        抓取全部（或指定）网站
  python -m skideals check-site <网址>     检测一个网站是否可信
  python -m skideals check-retailers       重新核验注册表里的全部网站
  python -m skideals stats                 查看数据库统计
  python -m skideals discover <店铺网址>   Shopify 店铺：自动找出各分类的 collection，生成 categories.toml 配置片段
  python -m skideals renormalize           标准化规则改进后，用新规则重算已有商品的型号/分组（不用重新抓取）
  python -m skideals audit [分类…]          数据质量自检：可能分错类、品牌写法乱、价格异常的商品
  python -m skideals specs                 双板规格补全：缺腰宽的型号去商品页规格表里读
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 控制台默认 GBK，避免中文/特殊字符报错
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="skideals", description="滑雪装备比价工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="启动网页")
    s.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    s.add_argument("--port", type=int, default=None)
    c = sub.add_parser("crawl", help="抓取价格")
    c.add_argument("ids", nargs="*", help="只抓这些零售商 id（默认全部）")
    c.add_argument("-c", "--category", action="append", help="只抓这些分类（可重复），如 -c jacket -c goggle")
    k = sub.add_parser("check-site", help="检测网站可信度")
    k.add_argument("url")
    sub.add_parser("check-retailers", help="核验注册表里的全部网站")
    sub.add_parser("stats", help="数据库统计")
    d = sub.add_parser("discover", help="Shopify 店铺分类自动发现")
    d.add_argument("url")
    sub.add_parser("renormalize", help="用新规则重算已有商品的型号/分组")
    sub.add_parser("specs", help="双板规格补全（读商品页里的腰宽）")
    a = sub.add_parser("audit", help="数据质量自检")
    a.add_argument("categories", nargs="*")
    args = ap.parse_args(argv)

    from . import db
    db.init()
    if args.cmd == "serve":
        from .server import serve
        serve(open_browser=args.open, port=args.port)
    elif args.cmd == "crawl":
        from .crawl import run_crawl
        summary = run_crawl(args.ids or None, args.category or None)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    elif args.cmd == "check-site":
        from .trust import check_site, format_report
        print(format_report(check_site(args.url)))
    elif args.cmd == "check-retailers":
        from .trust import check_all_retailers
        check_all_retailers(verbose=True)
    elif args.cmd == "discover":
        from .discover import discover
        print(discover(args.url))
    elif args.cmd == "renormalize":
        from .maintenance import renormalize
        renormalize()
    elif args.cmd == "specs":
        from .specs import backfill
        backfill()
    elif args.cmd == "audit":
        from .audit import run
        print(run(args.categories or None))
    elif args.cmd == "stats":
        with db.connect() as conn:
            for row in conn.execute("""SELECT r.id, r.last_status, COUNT(l.id) n,
                                        SUM(l.available) avail, SUM(l.compare_at IS NOT NULL) disc
                                       FROM retailers r LEFT JOIN listings l ON l.retailer_id=r.id AND l.active=1
                                       GROUP BY r.id ORDER BY n DESC"""):
                print(dict(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
