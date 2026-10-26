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
  python -m skideals snapshot export       数据库 → snapshot/（仓库里的文本快照，不含关注/提醒/设置）
  python -m skideals snapshot import       snapshot/ → 数据库（新电脑 clone 下来后还原全部历史数据）
  python -m skideals build-site            生成在线只读版静态网站（默认从 snapshot/ 还原，输出到 public/）
  python -m skideals publish               导出快照并提交、推送到 GitHub（Vercel 收到后自动重新部署）
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
    rn = sub.add_parser("renormalize", help="用新规则重算已有商品的型号/分组")
    rn.add_argument("--brand", help="只整理一个品牌，适合局部规则修复")
    sub.add_parser("specs", help="双板规格补全（读商品页里的腰宽）")
    a = sub.add_parser("audit", help="数据质量自检")
    a.add_argument("categories", nargs="*")
    sn = sub.add_parser("snapshot", help="数据快照：导出 / 还原")
    sn.add_argument("action", choices=["export", "import"])
    sn.add_argument("--dir", help="快照目录（默认 snapshot/）")
    sn.add_argument("--db", help="数据库文件（默认 data/skideals.db）")
    sn.add_argument("--force", action="store_true", help="import 时覆盖已有数据（会先备份）")
    bs = sub.add_parser("build-site", help="生成在线只读版静态网站")
    bs.add_argument("--out", default="public", help="输出目录（默认 public/）")
    bs.add_argument("--snapshot", help="快照目录（默认 snapshot/）")
    bs.add_argument("--db", help="直接用这个数据库生成，不从快照还原（本地预览用）")
    pb = sub.add_parser("publish", help="导出快照并提交、推送")
    pb.add_argument("--no-push", action="store_true", help="只提交，不推送")
    pb.add_argument("--force", action="store_true", help="抓取还在进行时也导出")
    args = ap.parse_args(argv)

    if args.cmd == "build-site":   # 必须在导入 db/config 之前设好环境变量
        return _build_site(args)

    if args.cmd in ("serve", "crawl"):  # 新 clone 下来第一次运行：先用仓库里的快照把数据库建起来
        from . import snapshot
        snapshot.bootstrap_if_empty()
    from . import db
    db.init()
    if args.cmd == "snapshot":
        from . import snapshot
        if args.action == "export":
            snapshot.export(args.db, args.dir)
        else:
            snapshot.import_(args.dir, args.db, force=args.force)
    elif args.cmd == "publish":
        return _publish(args)
    elif args.cmd == "serve":
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
        renormalize(brand=args.brand)
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


def _build_site(args) -> int:
    import os
    import tempfile
    from datetime import datetime
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    snap = Path(args.snapshot or root / "snapshot")
    os.environ["SKIDEALS_OFFLINE"] = "1"   # 汇率等只用快照里的，构建时不联网
    if args.db:
        os.environ["SKIDEALS_DB"] = str(Path(args.db).resolve())
        import sqlite3
        with sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True) as c:
            day = (c.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0] or "")[:10]
        info = {"data_day": day or None, "exported_at": None}
    else:
        manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
        tmp = Path(tempfile.mkdtemp(prefix="skideals-build-")) / "site.db"
        os.environ["SKIDEALS_DB"] = str(tmp)
        info = {"data_day": manifest.get("data_day"), "exported_at": manifest.get("exported_at")}
    if info["data_day"]:
        os.environ["SKIDEALS_TODAY"] = info["data_day"]   # “几天前降价”按数据日期算，不按构建那天算
    info["built_at"] = datetime.now().isoformat(timespec="seconds")
    from . import snapshot
    if not args.db:
        snapshot.import_(snap, os.environ["SKIDEALS_DB"])
    from . import db, site
    db.init()
    site.build(args.out, info)
    return 0


def _publish(args) -> int:
    """导出快照 → git 只提交 snapshot/ → 推送。代码改动不会被顺带提交。"""
    import subprocess
    from pathlib import Path
    from . import db, snapshot
    root = Path(__file__).resolve().parent.parent
    with db.connect() as conn:
        run = conn.execute("SELECT id, started_at FROM crawl_runs WHERE status='running' "
                           "AND started_at > datetime('now', 'localtime', '-6 hours') ORDER BY id DESC LIMIT 1").fetchone()
    if run and not args.force:
        print(f"抓取还在进行（{run['started_at']} 开始），等它结束再发布；确定要现在发布请加 --force")
        return 1
    manifest = snapshot.export()

    def git(*a, check=True):
        return subprocess.run(["git", *a], cwd=root, check=check, text=True, encoding="utf-8",
                              capture_output=True)

    git("add", "-A", "--", "snapshot")
    if not git("diff", "--cached", "--quiet", "--", "snapshot", check=False).returncode:
        print("快照和上次一样，没有需要提交的变化")
        return 0
    msg = f"数据快照 {manifest.get('data_day') or ''}".strip()
    r = git("commit", "-m", msg, "--", "snapshot", check=False)
    if r.returncode:
        print(r.stdout + r.stderr)
        return r.returncode
    print(f"已提交：{msg}")
    if args.no_push:
        return 0
    r = git("push", check=False)
    print((r.stdout + r.stderr).strip() or "已推送")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
