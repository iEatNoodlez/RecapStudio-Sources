"""FanFox (MangaFox): chapters from fanfox.net, a site built on the MangaHere/MangaFox platform (one company's two manga sites).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/mangahere.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'FanFox (MangaFox)'
VERSION = '1.0.0'
SITES = ['fanfox.net']
LANGUAGE = 'en'
NSFW = False
HEADERS = {'Referer': 'https://fanfox.net/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}]
DELAY = 0
BASE = 'https://fanfox.net'
OPTIONS = {}

# engine: MangaHere
# version: 1.0.0
# title: the MangaHere/MangaFox platform (one company's two manga sites)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# The site's own search, its directory sorted by popularity and by latest chapter, a series' chapters (all of its
# tabs: FanFox splits them into chapters with and without a volume) and a chapter's pictures. The reader hides
# those in packed scripts (eval(function(p,a,c,k,e,d)...)): either the chapter's page has them all (newImgs, mostly
# webtoons), or chapterfun.ashx gives them a page at a time with the key the page hides. Adult series need the
# site's isAdult cookie. No OPTIONS.
import re
import struct
import time
from urllib.parse import quote_plus, urljoin, urlparse

import requests

# ---- dom: a page's HTML as a tree, searched with CSS selectors (the standard library only: plugins have no other
# parser). parse(text) gives the document; node.select("css") its matches, node.one("css") the first, node.text the
# text inside, node.get("attr") an attribute. Selectors: tag, *, .class, #id, [attr], [attr=v], [attr^=v], [attr$=v],
# [attr*=v], [attr~=v], combined with descendant (space) and child (>) combinators, and groups (a, b).
import html as _html
import re as _re
from html.parser import HTMLParser as _HTMLParser
from urllib.parse import urljoin as _urljoin

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HIDDEN = {"script", "style", "noscript", "template"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs or {}, [], parent

    def get(self, name, default=""):
        value = self.attrs.get(name)
        return value if value not in (None, "") else default

    @property
    def classes(self):
        return set(self.attrs.get("class", "").split())

    def elements(self):
        """Every element inside, in document order."""
        stack = [c for c in reversed(self.children) if isinstance(c, Node)]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, Node))

    @property
    def text(self):
        """The text inside, with runs of white space as one space (scripts and styles left out)."""
        out, stack = [], list(reversed(self.children))
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                out.append(item)
            elif item.tag not in _HIDDEN:
                if item.tag in ("br", "p", "div", "li"):
                    out.append(" ")
                stack.extend(reversed(item.children))
        return _re.sub(r"\s+", " ", "".join(out)).strip()

    @property
    def own_text(self):
        """Its own text, without that of the elements inside it (a heading without its badges)."""
        return _re.sub(r"\s+", " ", "".join(c for c in self.children if isinstance(c, str))).strip()

    def select(self, css):
        chains = _selector(css)
        return [n for n in self.elements() if any(_chain(n, chain, len(chain) - 1) for chain in chains)]

    def one(self, css):
        chains = _selector(css)
        return next((n for n in self.elements() if any(_chain(n, chain, len(chain) - 1) for chain in chains)), None)

    def __repr__(self):
        return f"<{self.tag} {self.attrs}>"


class _Builder(_HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#document")

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:  # (an end tag nothing opened is passed over)
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(text):
    builder = _Builder()
    builder.feed(text or "")
    builder.close()
    return builder.root


_SIMPLE = _re.compile(r"""([a-zA-Z][\w-]*|\*)|\.([\w-]+)|\#([\w-]+)|\[\s*([\w:-]+)\s*(?:([~^$*]?=)\s*("[^"]*"|'[^']*'|[^\]\s]+)\s*)?\]""")
_cache = {}


def _compound(text):
    tests, pos = [], 0
    for m in _SIMPLE.finditer(text):
        if m.start() != pos:
            break
        pos = m.end()
        tag, cls, ident, attr, op, value = m.groups()
        if tag:
            tests.append(("tag", tag.lower()))
        elif cls:
            tests.append(("class", cls))
        elif ident:
            tests.append(("attr", "id", "=", ident))
        else:
            tests.append(("attr", attr.lower(), op, (value or "").strip("\"'")))
    if pos != len(text) or not tests:
        raise ValueError(f"A selector this parser can't read: {text!r}")
    return tests


def _selector(css):
    if css not in _cache:
        chains = []
        for group in css.split(","):
            tokens = _re.sub(r"\s*>\s*", " > ", group.strip()).split()
            chain, comb = [], " "
            for token in tokens:
                if token == ">":
                    comb = ">"
                    continue
                chain.append((comb, _compound(token)))
                comb = " "
            if chain:
                chains.append(chain)
        _cache[css] = chains
    return _cache[css]


def _matches(node, tests):
    if node.tag.startswith("#"):
        return False
    for test in tests:
        if test[0] == "tag":
            if test[1] != "*" and node.tag != test[1]:
                return False
        elif test[0] == "class":
            if test[1] not in node.classes:
                return False
        else:
            _, name, op, want = test
            if name not in node.attrs:
                return False
            have = node.attrs[name] or ""
            if op and not (op == "=" and have == want or op == "^=" and want and have.startswith(want)
                           or op == "$=" and want and have.endswith(want) or op == "*=" and want and want in have
                           or op == "~=" and want in have.split()):
                return False
    return True


def _chain(node, chain, i):
    comb, tests = chain[i]
    if not _matches(node, tests):
        return False
    if i == 0:
        return True
    if comb == ">":
        return node.parent is not None and _chain(node.parent, chain, i - 1)
    up = node.parent
    while up is not None:
        if _chain(up, chain, i - 1):
            return True
        up = up.parent
    return False


def absolute(base, url):
    """A link as a full one, with the spaces and line breaks some sites leave around it taken out."""
    url = _html.unescape((url or "").strip())
    return _urljoin(base, url) if url else ""


def picture(img, base=""):
    """An <img>'s picture: its lazy-loading attributes first (the src is often a placeholder), then srcset's first."""
    if img is None:
        return ""
    for name in ("data-src", "data-lazy-src", "lazy-src", "data-original", "data-cfsrc", "data-url", "src"):
        value = img.get(name).strip()
        if value and not value.startswith("data:"):
            return absolute(base, value)
    srcset = (img.get("data-srcset") or img.get("srcset")).strip()
    return absolute(base, srcset.split(",")[0].split()[0]) if srcset else ""


def number(name):
    """A chapter's number from its name ("Chapter 12.5 - The End" → 12.5), or None."""
    m = (_re.search(r"(?:chapter|chap|ch|episode|ep|#)\.?\s*(\d+(?:[.,]\d+)?)", name or "", _re.I)
         or _re.search(r"(\d+(?:\.\d+)?)", name or ""))
    return float(m.group(1).replace(",", ".")) if m else None
# ---- end of dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
_session.cookies.set("isAdult", "1")  # (what "I'm 18 or over" on an adult series sets)
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _get(url, **kw):
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _on_site(url):
    """A link on the site (its phone site's too) as one on BASE, and a series' link from one of its chapters."""
    path = urlparse(url).path
    m = re.match(r"/manga/([^/]+)", path)
    return f"{BASE}/manga/{m.group(1)}/" if m else BASE + path


def _word(n, radix):
    """The packer's name for word n (its e(): base `radix`, digits past z as chr(n + 29))."""
    out = ""
    while True:
        n, c = divmod(n, radix)
        out = (chr(c + 29) if c > 35 else _DIGITS[c]) + out
        if n == 0:
            return out


def _unpack(script):
    """The code an eval(function(p,a,c,k,e,d)...) script runs, with its words put back in."""
    m = re.search(r"\}\('(.*)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)", script, re.S)
    if not m:
        return ""
    code, radix, count, words = re.sub(r"\\(.)", r"\1", m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).split("|")
    table = {_word(i, radix): words[i] for i in range(min(count, len(words))) if words[i]}
    return re.sub(r"\b\w+\b", lambda w: table.get(w.group(0), w.group(0)), code, flags=re.A)


def _packed(text):
    """Every packed script in a page, unpacked."""
    return [_unpack(m.group(0)) for m in re.finditer(r"eval\(function\(p,a,c,k,e,d\).*?\.split\('\|'\)", text, re.S)]


def _strings(js):
    return re.findall(r"""["']([^"']*)["']""", js or "")


def _items(doc, css):
    """Search and list results: [{"url", "title", "cover", "summary"}]."""
    out, seen = [], set()
    for li in doc.select(css):
        link = li.one("p[class$=item-title] a") or li.one("a[href]")
        url = absolute(BASE, link.get("href")) if link else ""
        if not url or url in seen or "/manga/" not in url:
            continue
        seen.add(url)
        tips = [p.text for p in li.select("p[class$=item-tip]")]  # (Author:, Latest Chapter:, then the summary)
        summary = tips[-1] if tips and not re.match(r"(Author|Latest Chapter)\s*:", tips[-1]) else ""
        out.append({"url": url, "title": link.get("title") or link.text, "cover": picture(li.one("img"), BASE),
                    "summary": summary})
    return out


def _more(doc):
    """A "next page" arrow that goes somewhere (on the last page it's a dead link)."""
    return any(a.text == ">" and not a.get("href").startswith("javascript") for a in doc.select(".pager-list-left a"))


def search(query, page=1):
    doc = parse(_get(f"{BASE}/search?title={quote_plus(query)}&page={page}").text)
    items = _items(doc, "ul.manga-list-4-list > li")
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    sort = {"popular": "", "latest": "?latest"}[list_id]  # (the directory's Popularity and Chapters tabs)
    doc = parse(_get(f"{BASE}/directory/{page}.htm{sort}").text)
    items = _items(doc, "ul.manga-list-1-list > li")
    return {"items": items, "more": bool(items) and _more(doc)}


def _chapters(doc):
    out, seen = [], set()
    for tab in doc.select("#chapterlist ul"):  # (each tab newest first)
        rows = []
        for link in tab.select("li > a[href]"):
            url = absolute(BASE, link.get("href"))
            if url in seen:
                continue
            seen.add(url)
            title, date = link.one("p.title3"), link.one("p.title2")
            name = title.text if title is not None else link.get("title")
            rows.append({"url": url, "name": name, "number": number(name), "date": date.text if date is not None else ""})
        out.extend(reversed(rows))
    if all(c["number"] is not None for c in out):
        out.sort(key=lambda c: c["number"])  # (the tabs mixed together, oldest first)
    return out


def series(url):
    url = _on_site(url)
    doc = parse(_get(url).text)
    title = doc.one("span.detail-info-right-title-font")
    if title is None:
        raise ValueError(f"{NAME} has no series at {url}")
    summary = doc.one("p.fullcontent") or doc.one("p.detail-info-right-content")
    tags = [a.text.lower() for a in doc.select(".detail-info-right-tag-list a")]
    chapters = _chapters(doc)
    if not chapters and doc.one(".detail-block-content") is not None:
        raise RuntimeError(f"{NAME} keeps this series' chapters from readers: {doc.one('.detail-block-content').text}")
    return {"url": url, "title": title.text, "cover": picture(doc.one("img.detail-info-cover-img"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": "webtoon" if "webtoons" in tags else "manga", "chapters": chapters}


def _from_chapterfun(page_url, text, scripts):
    """The pictures chapterfun.ashx gives, a page at a time: each answer is that page's picture and the next one's."""
    cid = re.search(r"\bchapterid\s*=\s*(\d+)", text)
    count = re.search(r"\bimagecount\s*=\s*(\d+)", text)
    if not cid or not count:
        return []
    key = next((m.group(1) for m in (re.search(r"guidkey\s*=\s*([^;]*);", s) for s in scripts) if m), "")
    key = "".join(_strings(key))  # (its letters added up one by one: '' + '1' + '3' + 'b'...)
    ajax = {"Referer": page_url, "X-Requested-With": "XMLHttpRequest"}
    found, page, total = {}, 1, int(count.group(1))
    while page <= total:
        if page not in found:
            for attempt in range(2):
                code = _unpack(_get(urljoin(page_url, "chapterfun.ashx"), headers=ajax,
                                    params={"cid": cid.group(1), "page": page, "key": key}).text)
                if code:
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f"{NAME} gave nothing for page {page} of this chapter (it may be limiting "
                                   f"requests): try again in a minute")
            pix = _strings((re.search(r"\bpix\s*=\s*([\"'][^\"']*[\"'])", code) or [None, ""])[1])
            values = _strings((re.search(r"\bpvalue\s*=\s*\[([^\]]*)\]", code) or [None, ""])[1])
            first = re.search(r"pvalue\[i\]\s*=\s*[\"']([^\"']*)[\"']\s*\+\s*pvalue\[i\]", code)
            for i, value in enumerate(values):
                if not value.startswith(("http", "//")):
                    value = (first.group(1) if i == 0 and first else pix[0] if pix else "") + value
                found.setdefault(page + i, absolute(page_url, value))
            if not values:
                raise RuntimeError(f"{NAME} answered in a way this plugin doesn't know for page {page} of this chapter")
        page += 1
    return [found[n] for n in sorted(found) if n <= total]


def _banner(url):
    """Is it the site's 1000×563 banner it puts after a chapter's last page (a PNG, whatever its name)?"""
    try:
        with _session.get(url, timeout=20, stream=True) as r:
            head = r.raw.read(24)
    except requests.RequestException:
        return False
    return head[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", head[16:24]) == (1000, 563)


def pages(chapter_url):
    r = _get(BASE + urlparse(chapter_url).path if "://" in chapter_url else absolute(BASE, chapter_url))
    page_url, text = r.url, r.text
    scripts = _packed(text)
    found = []
    for code in scripts:  # (all of them in the page: newImgs = ['//...', ...])
        m = re.search(r"newImgs\s*=\s*\[([^\]]*)\]", code)
        if m:
            found = [absolute(page_url, u) for u in _strings(m.group(1))]
            break
    if not found:
        found = _from_chapterfun(page_url, text, scripts)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter (its reader may have changed)")
    if len(found) > 1 and _banner(found[-1]):
        found.pop()
    return found
