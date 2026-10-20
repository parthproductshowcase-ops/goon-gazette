"""Stamp a content hash onto CSS/JS links so browsers fetch fresh copies after a change.

GitHub Pages serves files with a 10-minute cache, so without this a style or script change
can take that long to show up. Run before committing any change to docs/assets or docs/themes:

    python3 -m pipeline.stamp_assets

It rewrites href/src in docs/*.html (and the @import in grandprix.css) to `file?v=<hash>`.
Idempotent: unchanged files keep their stamp.
"""
import hashlib
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
ASSETS = ["assets/gazette.css", "assets/common.js", "themes/fight.css", "themes/grandprix.css"]


def h(rel):
    return hashlib.sha1((DOCS / rel).read_bytes()).hexdigest()[:8]


def stamp(text, rel, ver, prefix=""):
    pat = re.compile(r'((?:href|src)=")' + re.escape(prefix + rel) + r'(\?v=[0-9a-f]+)?(")')
    return pat.sub(lambda m: f"{m.group(1)}{prefix}{rel}?v={ver}{m.group(3)}", text)


def main():
    # fight.css is imported by grandprix.css, so stamp that import first (it changes grandprix's hash)
    gp = DOCS / "themes/grandprix.css"
    g = gp.read_text()
    g2 = re.sub(r'@import url\("fight\.css(\?v=[0-9a-f]+)?"\);', f'@import url("fight.css?v={h("themes/fight.css")}");', g)
    if g2 != g:
        gp.write_text(g2)
    vers = {rel: h(rel) for rel in ASSETS}
    for page in sorted(DOCS.glob("*.html")):
        t = page.read_text()
        t2 = t
        for rel, v in vers.items():
            t2 = stamp(t2, rel, v)
        if t2 != t:
            page.write_text(t2)
            print("stamped", page.name)
    print("versions:", vers)


if __name__ == "__main__":
    main()
