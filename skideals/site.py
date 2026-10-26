"""在线只读版：把数据生成成静态网站（Vercel 直接托管，不需要服务器）。

    python -m skideals build-site [--out public] [--db 数据库]

默认从仓库里的 snapshot/ 还原到一个临时数据库再生成（Vercel 构建时就是这样做的）；本地预览也可以 --db 直接用本地库。
输出：
    public/                 网页文件（web/ 原样复制，index.html 里注入 window.SKIDEALS_STATIC）
    public/data/meta.json         数据日期、汇率、设置（不含任何个人数据）
    public/data/categories.json   分类与专区
    public/data/catalog/<分类>.json   和本地版 /api/catalog 完全相同，只多一个 ds（详情分片号）
    public/data/model/<分类>/<n>.json 每个分片约 40 个型号：{型号键: {"d": 详情, "i": 国际比价}}
    public/data/trust.json        已收录网站的核验结果

注意：调用前要先设置好环境变量（SKIDEALS_DB / SKIDEALS_TODAY / SKIDEALS_OFFLINE），所以 __main__ 里先设环境再导入本模块。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .config import WEB_DIR

SHARD = 40   # 每个详情分片的型号数：一个分片约 200 KB（压缩后约 60 KB），打开详情只下载这一个


def _dump(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    path.write_text(data, encoding="utf-8")
    return len(data)


def build(out_dir: Path | str, info: dict, log=print) -> dict:
    from . import catalog, server
    from .categories import BY_ID
    from .intl import service as intl

    t0 = time.time()
    catalog.FROZEN = True   # 构建期间数据不变，跳过每次的更新检查
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out)
    data = out / "data"

    cats = server.api_categories()
    _dump(data / "categories.json", cats)

    meta = server.api_meta()
    meta.update(crawl_running=False, unseen_alerts=0, n_watch=0, static=True, **info)
    # 国际比价里每个型号都一样的部分（汇率、说明）只存一份
    common = None

    n_models = n_bytes = 0
    failed: list[str] = []
    for cid in BY_ID:
        cat = catalog.build(cid)
        fams = cat["families"]
        shards: list[dict] = []
        for i, fam in enumerate(fams):
            fam["ds"] = i // SHARD
            if i % SHARD == 0:
                shards.append({})
            key = fam["k"]
            try:
                d = server.api_model(key)
                it = intl.get(key)
            except Exception as e:   # 单个型号出错不影响整站；记下来最后报告
                failed.append(f"{key}: {e!r}")
                continue
            if common is None:
                common = {"notes": it.get("notes"), "fx": it.get("fx")}
            slim = {k: v for k, v in it.items() if k not in ("notes", "fx")}
            shards[-1][key] = {"d": d, "i": slim}
            n_models += 1
        for n, shard in enumerate(shards):
            n_bytes += _dump(data / "model" / cid / f"{n}.json", shard)
        n_bytes += _dump(data / "catalog" / f"{cid}.json", cat)
        log(f"  {cid:16s} {len(fams):5d} 个型号，{len(shards)} 个分片")

    meta["intl_common"] = common or {}
    _dump(data / "meta.json", meta)
    _dump(data / "trust.json", server.api_trust_retailers())

    # 让页面知道自己是在线只读版（放在 app.js 之前）
    index = out / "index.html"
    flag = json.dumps({"data_day": info.get("data_day"), "built_at": info.get("built_at")}, ensure_ascii=False)
    html = index.read_text(encoding="utf-8")
    html = html.replace('<script src="icons.js"></script>',
                        f'<script>window.SKIDEALS_STATIC = {flag};</script>\n<script src="icons.js"></script>', 1)
    index.write_text(html, encoding="utf-8")

    if failed:
        log(f"⚠ {len(failed)} 个型号的详情生成失败（页面会提示“型号不存在”）：" + "；".join(failed[:5]))
    log(f"静态网站已生成到 {out}：{n_models} 个型号，数据 {n_bytes / 1e6:.0f} MB，用时 {time.time() - t0:.0f} 秒")
    return {"models": n_models, "bytes": n_bytes, "failed": failed}
