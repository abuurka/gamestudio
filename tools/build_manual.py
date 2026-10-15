#!/usr/bin/env python3
"""Validate the illustrated manual and build its offline HTML edition.

    python -m pip install -r tools/manual-requirements.txt
    python tools/build_manual.py
    python tools/build_manual.py --check

The check mode is read-only and also checks that the committed HTML is current.
Images must match the visually reviewed inventory in docs/manual/screenshots.json.
No network requests, image editing, user-profile capture or timestamps are used.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote, urlsplit

try:
    from markdown_it import MarkdownIt
    from PIL import Image
except ImportError:
    raise SystemExit("Install documentation dependencies: python -m pip install -r tools/manual-requirements.txt")


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MANUAL = DOCS / "manual"
OUTPUT = DOCS / "manual.html"
MD = MarkdownIt("commonmark", {"html": False}).enable("table")
CHAPTERS = [MANUAL / "README.md", *sorted(MANUAL.glob("[0-9][0-9]-*.md"))]
VERSION = tomllib.loads((ROOT / "blender" / "gamestudio" / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]


def label(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def slug(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def chapter_id(path: Path) -> str:
    return "overview" if path.name == "README.md" else "chapter-" + path.stem


def headings(tokens):
    counts = {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            title = tokens[index + 1].content
            base = slug(title)
            number = counts.get(base, 0)
            counts[base] = number + 1
            yield token, title, base + (f"-{number}" if number else "")


def inline_tokens(tokens):
    for token in tokens:
        if token.children:
            yield from token.children


def local_target(source: Path, url: str):
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return None
    target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
    if not target.is_relative_to(ROOT):
        raise ValueError(f"{label(source)}: link leaves the repository")
    return target, unquote(parts.fragment)


def privacy_check(text: str, source: Path) -> list[str]:
    patterns = {
        "absolute Windows path": r"\b[A-Za-z]:[\\/]",
        "home-directory path": r"/(?:Users|home)/[^\s/]+",
        "email address": r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b",
        "credential-shaped value": r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}",
    }
    errors = [f"{label(source)}: review {kind}" for kind, pattern in patterns.items()
              if re.search(pattern, text)]
    # Do not write a local account name into the report itself.
    names = {os.environ.get("USERNAME", ""), os.environ.get("USER", ""), Path.home().name}
    for name in names:
        if len(name) >= 4 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            errors.append(f"{label(source)}: review local account identifier")
            break
    return errors


def validate():
    errors = []
    inventory_path = MANUAL / "screenshots.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    approved = {ROOT / entry["path"]: entry for entry in inventory["images"]}
    all_sources = [DOCS / "README.md", *sorted(MANUAL.glob("*.md"))]
    parsed = {p: MD.parse(p.read_text(encoding="utf-8")) for p in all_sources}
    anchors = {p: {s for _, _, s in headings(ts)} for p, ts in parsed.items()}
    used = set()
    link_count = 0
    for source, tokens in parsed.items():
        errors.extend(privacy_check(source.read_text(encoding="utf-8"), source))
        for token in tokens:
            if token.type == "fence" and token.info == "json":
                try:
                    json.loads(token.content)
                except json.JSONDecodeError:
                    errors.append(f"{label(source)}: invalid JSON example")
        for token in inline_tokens(tokens):
            if token.type not in ("link_open", "image"):
                continue
            link_count += 1
            url = token.attrGet("src" if token.type == "image" else "href")
            try:
                resolved = local_target(source, url)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if resolved is None:
                if token.type == "image":
                    errors.append(f"{label(source)}: remote image is not allowed")
                continue
            target, fragment = resolved
            if not target.exists() and target != OUTPUT:
                errors.append(f"{label(source)}: missing target {label(target)}")
            if fragment and target.suffix == ".md" and target.exists():
                if target not in anchors:
                    anchors[target] = {s for _, _, s in headings(MD.parse(target.read_text(encoding="utf-8")))}
                if fragment not in anchors[target]:
                    errors.append(f"{label(source)}: missing anchor in {label(target)}: {fragment}")
            if token.type == "image":
                used.add(target)
                if not token.content.strip():
                    errors.append(f"{label(source)}: image needs alternative text")
                if target not in approved:
                    errors.append(f"{label(source)}: image requires visual privacy review: {label(target)}")

    for path in sorted(used):
        if not path.exists() or path not in approved:
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != approved[path]["sha256"]:
            errors.append(f"{label(path)}: changed since visual privacy review")
        with Image.open(path) as picture:
            picture.verify()
        with Image.open(path) as picture:
            if picture.format != "PNG":
                errors.append(f"{label(path)}: expected PNG")
            # These captures contain color/resolution metadata only. Fail closed
            # on new textual, GPS, author, serial-number or application metadata.
            allowed_info = {"srgb", "gamma", "chromaticity", "dpi", "exif", "icc_profile"}
            if set(picture.info) - allowed_info:
                errors.append(f"{label(path)}: image metadata needs review")
            if set(picture.getexif()) - {282, 283, 296}:
                errors.append(f"{label(path)}: EXIF metadata needs review")
    if set(approved) != used:
        errors.append("Screenshot inventory and manual image references differ")
    if errors:
        raise ValueError("\n".join(errors))
    return parsed, len(used), link_count


CSS = """
:root{color-scheme:light;--ink:#17313a;--muted:#53676e;--accent:#087b78;--line:#d8e4e5;--paper:#fff;--wash:#f3f7f7}
*{box-sizing:border-box}html{scroll-padding-top:26px}body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.72 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:#086f73;text-underline-offset:3px}a:hover{color:#00484c}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #edaa4f;outline-offset:4px}
.skip{position:fixed;left:16px;top:-80px;background:white;padding:12px;z-index:5}.skip:focus{top:12px}
aside{position:fixed;inset:0 auto 0 0;width:270px;background:#17313a;color:white;padding:30px 22px;overflow-y:auto}
.brand{font-size:14px;font-weight:800;letter-spacing:.16em;color:#8ee1d1}.edition{font-size:12px;color:#c6d7db;margin:6px 0 24px}
nav a{display:block;color:#e6f1f3;text-decoration:none;font-size:14px;line-height:1.45;padding:10px 12px;border-radius:6px;margin:3px 0}
nav a:hover,nav a:focus{background:#294a53;color:white}nav a.current{background:#315b62;border-left:3px solid #8ee1d1;padding-left:9px}
.nav-number{color:#8ee1d1;margin-right:8px;font-variant-numeric:tabular-nums}.nav-note{font-size:12px;color:#c6d7db;margin:24px 12px 0}
main{margin-left:270px;padding:48px 56px 90px;max-width:1400px}.masthead{border-top:5px solid var(--accent);padding:30px 36px;background:#e4f1ef;border-radius:0 0 12px 12px;margin-bottom:42px}
.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.15em;font-weight:800;color:var(--accent)}h1{font-size:clamp(32px,4vw,48px);line-height:1.15;letter-spacing:-.035em;margin:12px 0 16px}
.masthead p{font-size:17px;margin:0;max-width:690px}.toolbar{display:flex;gap:16px;align-items:center;margin-top:22px;font-size:13px;flex-wrap:wrap}button{font:inherit;color:white;background:var(--accent);border:0;padding:10px 16px;border-radius:6px;cursor:pointer}
.chapter{background:var(--paper);padding:38px 42px;margin:0 0 36px;border:1px solid var(--line);border-radius:10px;box-shadow:0 3px 16px #17313a04}
.chapter>p,.chapter>ul,.chapter>ol,.chapter>blockquote{max-width:820px}.chapter h2{font-size:32px;line-height:1.22;letter-spacing:-.02em;margin:0 0 20px}.chapter h3{font-size:22px;line-height:1.3;margin:38px 0 14px;padding-top:8px;border-top:1px solid var(--line)}.chapter h4{font-size:18px;margin-top:26px}
p{margin:14px 0}li{padding-left:3px;margin:7px 0}strong{font-weight:700}blockquote{border-left:4px solid var(--accent);background:#f0f7f5;padding:8px 22px;margin:22px 0;color:#234a50}
img{display:block;max-width:100%;height:auto;margin:22px auto 10px;border-radius:7px;border:1px solid #cbd8da}.js .chapter img{cursor:zoom-in}.chapter img:focus-visible,.table-scroll:focus-visible{outline:3px solid #edaa4f;outline-offset:4px}p:has(>img)+p:has(>em:only-child){font-size:13px;line-height:1.6;color:var(--muted);margin:8px auto 26px}
dialog{width:min(96vw,1640px);max-width:96vw;max-height:94vh;overflow:auto;border:0;border-radius:10px;padding:18px;background:#f3f7f7;color:var(--ink)}dialog::backdrop{background:#0b222be6}dialog img{margin:14px auto 0;max-width:100%}dialog button{position:sticky;top:0;display:block;margin-left:auto}
.table-scroll{overflow-x:auto;margin:24px 0;border:1px solid var(--line);border-radius:6px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.55;text-align:left}th{background:#eaf2f2;font-weight:700}td,th{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}tr:nth-child(even) td{background:#fafcfc}td img{min-width:150px;max-width:320px;width:100%;margin:0}td p{margin:0}
code{font: .88em/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;background:#edf3f4;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{background:#17313a;color:#e9f4f5;padding:22px 24px;border-radius:7px;overflow-x:auto;font-size:14px;line-height:1.65}pre code{color:inherit;background:none;padding:0;overflow-wrap:normal}
.endnote{font-size:13px;color:var(--muted);padding:0 8px}details.mobile-contents{display:none}#print-button{display:none}.js #print-button{display:inline-block}
@media(min-width:1600px){main{margin-left:max(270px,calc((100vw - 1120px)/2));padding-left:40px;padding-right:40px}}
@media(max-width:1080px){aside{width:235px;padding:24px 14px}main{margin-left:235px;padding:30px 25px}.chapter{padding:28px}.masthead{padding:28px}}
@media(max-width:760px){aside{display:none}main{margin:0;padding:18px 12px 50px}.masthead{padding:24px 20px;margin-bottom:20px}.chapter{padding:24px 18px;margin-bottom:22px;border-radius:7px}.chapter h2{font-size:28px}.chapter h3{font-size:21px}details.mobile-contents{display:block;margin:20px 0;background:white;border:1px solid var(--line);padding:14px;border-radius:7px}summary{cursor:pointer;font-weight:700}details nav a{color:var(--ink)}details nav a:hover{background:#e4f1ef;color:var(--ink)}details .nav-number{color:var(--accent)}pre{padding:16px;font-size:12px}td,th{padding:10px;min-width:120px}table{font-size:13px}}
@media print{@page{size:A4;margin:17mm}body{background:white;font-size:10pt;line-height:1.5}aside,.toolbar,details.mobile-contents,.skip{display:none!important}main{margin:0;padding:0;max-width:none}.masthead{margin:0 0 20px;padding:20px;background:white;border-bottom:1px solid #bbb}h1{font-size:30pt}.chapter{border:0;box-shadow:none;padding:0;margin:0;break-before:page}.chapter:first-of-type{break-before:auto}.chapter h2{font-size:23pt}.chapter h3{font-size:15pt;break-after:avoid}.chapter h4{break-after:avoid}p,li{orphans:3;widows:3}img{max-height:170mm;object-fit:contain;break-inside:avoid}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f4;color:#17313a;font-size:8.5pt;break-inside:avoid}pre code{overflow-wrap:anywhere}.table-scroll{overflow:visible}table{font-size:8pt}thead{display:table-header-group}tr{break-inside:avoid}td,th{padding:7px}a{color:inherit;text-decoration:none}.endnote{margin-top:20px}}
"""


def render(image_count):
    blocks, nav = [], []
    for source in CHAPTERS:
        tokens = MD.parse(source.read_text(encoding="utf-8"))
        section_id = chapter_id(source)
        title = next(headings(tokens))[1]
        number = "" if source.name == "README.md" else source.stem[:2]
        nav.append(f'<a href="#{section_id}"><span class="nav-number">{number}</span>'
                   f'{html.escape("Overview" if not number else title)}</a>')
        for token, _, anchor in headings(tokens):
            token.attrSet("id", section_id + "--" + anchor)
        for token in tokens:
            if token.type in ("heading_open", "heading_close"):
                token.tag = "h" + str(min(int(token.tag[1:]) + 1, 6))
        for token in inline_tokens(tokens):
            if token.type == "image":
                path, _ = local_target(source, token.attrGet("src"))
                token.attrSet("src", "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii"))
                with Image.open(path) as picture:
                    token.attrSet("width", str(picture.width))
                    token.attrSet("height", str(picture.height))
                token.attrSet("loading", "lazy")
                token.attrSet("decoding", "async")
            elif token.type == "link_open":
                target = local_target(source, token.attrGet("href"))
                if target is None:
                    continue
                path, fragment = target
                if path in CHAPTERS:
                    url = "#" + chapter_id(path) + ("--" + fragment if fragment else "")
                elif path == OUTPUT:
                    url = "#overview"
                else:
                    url = os.path.relpath(path, DOCS).replace(os.sep, "/") + ("#" + fragment if fragment else "")
                token.attrSet("href", url)
        content = MD.renderer.render(tokens, MD.options, {})
        if source.name == "README.md":
            content = re.sub(r"^<h2[^>]*>.*?</h2>\n", "", content, count=1)
        content = content.replace("<table>", '<div class="table-scroll" tabindex="0" role="region" aria-label="Reference table"><table>')
        content = content.replace("</table>", "</table></div>")
        blocks.append(f'<section class="chapter" id="{section_id}" aria-label="{html.escape(title)}">{content}</section>')
    navigation = "\n".join(nav)
    body = "\n".join(blocks)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Illustrated GameStudio user manual: installation, game creation, components, assets, UI, agents, testing and web builds.">
<link rel="icon" href="images/logo-mark.svg" type="image/svg+xml"><title>GameStudio user manual</title><style>{CSS}</style></head><body>
<a class="skip" href="#main">Skip to manual</a>
<aside aria-label="Manual navigation"><div class="brand"><a href="index.html" style="color:inherit;text-decoration:none">GAMESTUDIO</a></div><div class="edition">User manual · Extension 0.1.0</div>
<nav aria-label="Chapters">{navigation}</nav><p class="nav-note">Use your browser's Find command to search the full manual.<br><br>All screenshots are embedded. No network connection is needed to read this edition.</p></aside>
<main id="main"><header class="masthead"><div class="eyebrow">Blender extension · Illustrated documentation</div>
<h1>GameStudio user manual</h1><p>From installation and your first playable level to reusable assets, game logic and a tested web build.</p>
<div class="toolbar"><button type="button" id="print-button">Print or save as PDF</button><span>10 chapters · {image_count} screenshots and previews · Version {VERSION}</span></div></header>
<details class="mobile-contents"><summary>Browse the chapters</summary><nav aria-label="Mobile chapters">{navigation}</nav></details>
{body}<footer class="endnote">Generated from the Markdown manual. Full technical references use repository-relative links and are available alongside this file in the source checkout.</footer></main>
<script>
document.documentElement.classList.add('js');
document.getElementById('print-button').addEventListener('click', () => window.print());
document.querySelectorAll('.chapter img').forEach(source => {{
  source.tabIndex = 0;
  source.setAttribute('role', 'button');
  source.setAttribute('aria-label', 'Enlarge image: ' + source.alt);
  function enlarge() {{
    const dialog = document.createElement('dialog');
    dialog.setAttribute('aria-label', source.alt);
    const close = document.createElement('button');
    close.textContent = 'Close image';
    close.addEventListener('click', () => dialog.close());
    const picture = document.createElement('img');
    picture.src = source.src;
    picture.alt = source.alt;
    dialog.append(close, picture);
    document.body.append(dialog);
    dialog.addEventListener('close', () => {{ dialog.remove(); source.focus({{preventScroll:true}}); }}, {{once:true}});
    dialog.showModal();
  }}
  source.addEventListener('click', enlarge);
  source.addEventListener('keydown', event => {{
    if (event.key === 'Enter' || event.key === ' ') {{ event.preventDefault(); enlarge(); }}
  }});
}});
const chapterLinks = [...document.querySelectorAll('aside nav a')];
if ('IntersectionObserver' in window) {{
  const observer = new IntersectionObserver(entries => {{
    const entry = entries.find(item => item.isIntersecting);
    if (entry) chapterLinks.forEach(link => {{
      const active = link.hash === '#' + entry.target.id;
      link.classList.toggle('current', active);
      if (active) link.setAttribute('aria-current', 'location'); else link.removeAttribute('aria-current');
    }});
  }}, {{ rootMargin: '-5% 0px -70% 0px', threshold: 0 }});
  document.querySelectorAll('.chapter').forEach(section => observer.observe(section));
}}
</script></body></html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Validate without changing files; fail if HTML is stale")
    args = parser.parse_args()
    try:
        _parsed, images, links = validate()
        output = render(images)
        if args.check:
            if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != output:
                raise ValueError("docs/manual.html is stale; run python tools/build_manual.py")
        else:
            OUTPUT.write_text(output, encoding="utf-8", newline="\n")
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    words = sum(len(path.read_text(encoding="utf-8").split()) for path in CHAPTERS)
    print(f"Manual validated: {len(CHAPTERS) - 1} chapters, {words:,} words, {images} reviewed images, {links} links.")
    print("Offline HTML is current." if args.check else "Built docs/manual.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
