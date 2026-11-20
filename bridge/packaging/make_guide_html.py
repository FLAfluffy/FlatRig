"""docs/AVATAR_GUIDE.md を、配布フォルダに入れる 1 枚の HTML(アバター作成ガイド.html)にする。

ブラウザでダブルクリックして読めるように、CSS は HTML の中に入れ、外部のファイルや通信は使わない。
使い方(単体で試すとき): python packaging/make_guide_html.py <入力.md> <出力.html>
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

CSS = """
:root {
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --soft: #f6f8fa;
  --accent: #0b6bcb; --code-bg: #eff2f5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15181c; --fg: #e6e8eb; --muted: #9aa4ae; --line: #343b43; --soft: #1d2126;
    --accent: #6cb2ff; --code-bg: #262b31;
  }
}
* { box-sizing: border-box; }
html { scroll-padding-top: 16px; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: "Yu Gothic UI", "Meiryo UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
  font-size: 16px; line-height: 1.8;
}
.layout { display: flex; max-width: 1180px; margin: 0 auto; gap: 40px; padding: 0 24px; }
nav {
  flex: 0 0 230px; position: sticky; top: 0; align-self: flex-start;
  max-height: 100vh; overflow-y: auto; padding: 32px 0; font-size: 14px;
}
nav p { margin: 0 0 8px; font-weight: bold; color: var(--muted); }
nav ol { list-style: none; margin: 0; padding: 0; }
nav li { margin: 0; }
nav a {
  display: block; padding: 4px 10px; border-left: 2px solid var(--line);
  color: var(--fg); text-decoration: none;
}
nav a:hover { border-left-color: var(--accent); color: var(--accent); }
main { flex: 1 1 auto; min-width: 0; padding: 32px 0 80px; }
h1 { font-size: 2em; margin: 0 0 16px; }
h2 { font-size: 1.5em; margin: 56px 0 16px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.2em; margin: 36px 0 12px; }
h4 { font-size: 1.05em; margin: 28px 0 8px; }
hr { display: none; }
a { color: var(--accent); }
code {
  font-family: Consolas, "Cascadia Mono", monospace; font-size: 0.9em;
  background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
}
ul, ol { padding-left: 1.6em; }
li { margin: 4px 0; }
li > ul, li > ol { margin: 4px 0; }
table { border-collapse: collapse; margin: 16px 0; display: block; overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid var(--line); padding: 6px 12px; text-align: left; vertical-align: top; }
th { background: var(--soft); white-space: nowrap; }
@media (max-width: 860px) {
  .layout { display: block; padding: 0 16px; }
  nav { position: static; max-height: none; padding: 24px 0 0; }
}
@media print {
  nav { display: none; }
  .layout { display: block; }
}
"""


def _slug(text: str, used: set[str]) -> str:
    """見出しのアンカー。「5.2 …」のような番号があれば sec-5-2、無ければ連番。"""
    m = re.match(r"付録\s*([A-Z])|([0-9]+(?:\.[0-9]+)*)", text.strip())
    base = "sec-" + (m.group(1) or m.group(2)).replace(".", "-") if m else f"sec-x{len(used) + 1}"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def render(md_text: str) -> str:
    from markdown_it import MarkdownIt  # 配布物を作るときだけ使う(pip install -e ".[build]")

    md = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("table")
    tokens = md.parse(md_text)
    used: set[str] = set()
    title = "FlatRig アバター作成ガイド"
    toc: list[tuple[str, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open":
            continue
        text = tokens[i + 1].content
        slug = _slug(text, used)
        tok.attrSet("id", slug)
        if tok.tag == "h1":
            title = text
        elif tok.tag == "h2":
            toc.append((slug, text))
    body = md.renderer.render(tokens, md.options, {})
    nav = "".join(f'<li><a href="#{html.escape(s, quote=True)}">{html.escape(t)}</a></li>' for s, t in toc)
    return (
        "<!doctype html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f'<div class="layout">\n<nav><p>目次</p><ol>{nav}</ol></nav>\n<main>\n{body}</main>\n</div>\n'
        "</body>\n</html>\n"
    )


def write(md_path: Path, out_path: Path) -> None:
    out_path.write_text(render(md_path.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    write(Path(sys.argv[1]), Path(sys.argv[2]))
