# -*- coding: utf-8 -*-
"""站点 URL / canonical / OG 的统一生成与校验工具。

用法：
  python tools/site_geo.py fix
  python tools/site_geo.py check

配置：tools/site_geo.config.json 的 siteUrl，或环境变量 SITE_URL。
只改 app/web 的静态页面/机读文件，不碰业务逻辑和后端接口。
"""
from __future__ import annotations
import argparse, html, json, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "Sanitized_Data" / "app" / "web"
CONFIG = ROOT / "tools" / "site_geo.config.json"
DEFAULT_SITE_URL = "https://sd.blctek.com"

# file, path, indexable
PUBLIC = [
    ("home.html", "/", True),
    ("demo.html", "/demo", True),
    ("download.html", "/download", True),
    ("mcp.html", "/mcp", True),
    ("pricing.html", "/pricing", True),
    ("license.html", "/license", True),
    ("purchase.html", "/purchase", False),
    ("faq.html", "/faq", True),
    ("compare.html", "/compare", True),
    ("support_matrix.html", "/support-matrix", True),
    ("security.html", "/security", True),
    ("changelog.html", "/changelog", True),
    ("cases.html", "/cases", True),
    ("docs.html", "/docs", True),
    ("for_lawyers.html", "/for/lawyers", True),
    ("for_medical.html", "/for/medical", True),
    ("for_geo.html", "/for/geo", True),
    ("privacy.html", "/privacy", False),
    ("eula.html", "/eula", False),
    ("404.html", "/404", False),
]

def site_url() -> str:
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/")
    try:
        if CONFIG.exists():
            return str(json.loads(CONFIG.read_text(encoding="utf-8"))["siteUrl"]).rstrip("/")
    except Exception:
        pass
    return DEFAULT_SITE_URL

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()

def page_meta(text: str, fallback_title: str):
    tm = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = clean_text(tm.group(1)) if tm else fallback_title
    dm = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', text, re.S | re.I)
    if not dm:
        dm = re.search(r'<meta\s+content=["\'](.*?)["\']\s+name=["\']description["\']', text, re.S | re.I)
    desc = clean_text(dm.group(1)) if dm else title
    return title, desc

def strip_existing_head(text: str) -> str:
    text = re.sub(r'\n?\s*<link\s+rel=["\']canonical["\'][^>]*>', "", text, flags=re.I)
    text = re.sub(r'\n?\s*<meta\s+property=["\']og:[^"\']+["\'][^>]*>', "", text, flags=re.I)
    text = re.sub(r'\n?\s*<meta\s+name=["\']twitter:[^"\']+["\'][^>]*>', "", text, flags=re.I)
    return text

def inject_head(text: str, page_path: str, title: str, desc: str, base: str) -> str:
    text = strip_existing_head(text)
    url = base + (page_path if page_path != "/" else "/")
    image = base + "/og.png"
    lines = [
        f'<link rel="canonical" href="{html.escape(url, quote=True)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="数据隐站">',
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
        f'<meta property="og:description" content="{html.escape(desc, quote=True)}">',
        f'<meta property="og:url" content="{html.escape(url, quote=True)}">',
        f'<meta property="og:image" content="{html.escape(image, quote=True)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(title, quote=True)}">',
        f'<meta name="twitter:description" content="{html.escape(desc, quote=True)}">',
        f'<meta name="twitter:image" content="{html.escape(image, quote=True)}">',
    ]
    # 页面没有 description 时补一条
    if not re.search(r'<meta\s+name=["\']description["\']', text, re.I):
        lines.insert(0, f'<meta name="description" content="{html.escape(desc, quote=True)}">')
    block = "\n".join(lines)
    if re.search(r"</head>", text, re.I):
        return re.sub(r"</head>", block + "\n</head>", text, count=1, flags=re.I)
    return text

def sitemap_xml(base: str) -> str:
    rows = []
    for fn, path, indexable in PUBLIC:
        if not indexable:
            continue
        url = base + (path if path != "/" else "/")
        rows.append("  <url>\n    <loc>" + html.escape(url) + "</loc>\n    <lastmod>2026-11-13</lastmod>\n    <priority>0.7</priority>\n  </url>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n"

def fix_robots(text: str, base: str) -> str:
    text = re.sub(r"(?mi)^Sitemap:.*$", "Sitemap: " + base + "/sitemap.xml", text)
    if "Sitemap:" not in text:
        text = text.rstrip() + "\n\nSitemap: " + base + "/sitemap.xml\n"
    return text

def fix_llms(text: str, base: str) -> str:
    text = re.sub(r"https?://sd\.blctek\.com(?::8443)?", base, text)
    if base + "/sitemap.xml" not in text:
        pass
    return text

def fix_all() -> None:
    base = site_url()
    for fn, path, _ in PUBLIC:
        p = WEB / fn
        if not p.exists():
            print("MISS", fn)
            continue
        text = p.read_text(encoding="utf-8")
        title, desc = page_meta(text, fn.replace(".html", ""))
        p.write_text(inject_head(text, path, title, desc, base), encoding="utf-8")
        print("head", fn)
    # 机读文件
    (WEB / "sitemap.xml").write_text(sitemap_xml(base), encoding="utf-8")
    (WEB / "robots.txt").write_text(fix_robots((WEB / "robots.txt").read_text(encoding="utf-8"), base), encoding="utf-8")
    (WEB / "llms.txt").write_text(fix_llms((WEB / "llms.txt").read_text(encoding="utf-8"), base), encoding="utf-8")
    # 只清理公开页面；index.html/restore.html 是本地 UI，不碰
    for fn, _, _ in PUBLIC:
        p = WEB / fn
        text = p.read_text(encoding="utf-8")
        text = text.replace("https://sd.blctek.com:8443", base).replace("http://sd.blctek.com:8443", base)
        p.write_text(text, encoding="utf-8")
    print("fixed", base)

def check() -> int:
    base = site_url()
    problems = []
    all_text = ""
    for fn, path, _ in PUBLIC:
        p = WEB / fn
        if not p.exists():
            problems.append("missing " + fn); continue
        t = p.read_text(encoding="utf-8"); all_text += t
        if base + (path if path != "/" else "/") not in t:
            problems.append("canonical/og url missing " + fn)
        if 'rel="canonical"' not in t:
            problems.append("canonical missing " + fn)
        if 'property="og:title"' not in t:
            problems.append("og:title missing " + fn)
        if ":8443" in t:
            problems.append("8443 in " + fn)
    for fn in ["sitemap.xml","robots.txt","llms.txt"]:
        t=(WEB/fn).read_text(encoding="utf-8"); all_text += t
        if ":8443" in t: problems.append("8443 in "+fn)
    if "Sitemap: " + base + "/sitemap.xml" not in (WEB/"robots.txt").read_text(encoding="utf-8"):
        problems.append("robots Sitemap wrong")
    if not (WEB/"og.png").exists():
        problems.append("og.png missing")
    if problems:
        print("FAIL")
        for x in problems[:30]: print(" -",x)
        return 1
    print("PASS", base)
    return 0

if __name__ == "__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("action",choices=["fix","check"]); a=ap.parse_args()
    if a.action=="fix": fix_all()
    else: raise SystemExit(check())