"""MangaDNA: chapters from mangadna.com (English manhwa, much of it adult).

Search is the site's /search?q= page (24 a page, paged with &page=); the lists are its /manga list in the site's own
orders (trending, latest, rating); a series' chapters are all on its page, newest first; a chapter's pictures are
lazy-loaded <img>s (data-src) in its reader.
"""
import re
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "MangaDNA"
VERSION = "1.0.0"
SITES = ["mangadna.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "popular", "name": "Trending"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://mangadna.com/"}
DELAY = 0
TEST_QUERY = "fist"

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

BASE = "https://mangadna.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
ORDER = {"popular": "trending", "latest": "latest", "rating": "rating"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
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


def _results(url):
    """Search and list results (the site's .home-item cards): {"items", "more"}."""
    doc = parse(_get(url).text)
    items, seen = [], set()
    for card in doc.select(".home-item"):
        link = card.one(".htitle a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one(".hthumb img") or card.one("img")
        items.append({"url": url, "title": link.get("title") or link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "webtoon"})
    more = doc.one("ul.pagination li.next a") is not None
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    values = {"q": query.strip()}
    if page > 1:
        values["page"] = page
    return _results(f"{BASE}/search?{urlencode(values)}")


def browse(list_id, page=1):
    if list_id not in ORDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    values = {"orderby": ORDER[list_id]}
    if page > 1:
        values["page"] = page
    return _results(f"{BASE}/manga?{urlencode(values)}")


def _series_url(url):
    """The series' link from its own or one of its chapters' (/manga/<slug>/chapter-<n>)."""
    m = re.match(r"https?://[^/]+/manga/([^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    return f"{BASE}/manga/{m.group(1)}"


def _date(text):
    """The site's "01 Nov 26" / "Nov 01, 2026" as "2026-11-01" (else as it is)."""
    text = (text or "").strip()
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})$", text) or re.match(r"([A-Za-z]{3})\w*\s+(\d{1,2}),\s*(\d{4})$", text)
    if not m:
        return text
    if m.group(1).isdigit():
        day, mon, year = m.group(1), m.group(2), m.group(3)
    else:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    if mon.lower() not in MONTHS:
        return text
    year = int(year)
    return f"{year + 2000 if year < 100 else year}-{MONTHS[mon.lower()]:02d}-{int(day):02d}"


def _detail(doc, label):
    """The value of a "Type" / "Release" row on a series' page."""
    for row in doc.select(".post-content_item"):
        heading, value = row.one(".summary-heading"), row.one(".summary-content")
        if heading is not None and value is not None and heading.text.rstrip(": ").lower() == label:
            return value.text
    return ""


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one(".post-title h1, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    about = doc.one(".panel-story-description")
    summary = ""
    if about is not None:  # ("Summary" heading, then the text)
        body = about.one("div, p")
        summary = body.text if body is not None else about.text.removeprefix("Summary").strip()
    year = _detail(doc, "release")
    kind = KINDS.get(_detail(doc, "type").strip().lower(), "webtoon")

    chapters, seen = [], set()
    for row in doc.select("ul.row-content-chapter li, li.a-h"):
        link = row.one("a.chapter-name") or row.one("a[href*='/chapter-']")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = link.text or link.get("title")
        when = row.one(".chapter-time")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": _date(when.get("title") or when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one(".summary_image img"), BASE),
            "summary": summary, "kind": kind, "year": int(year) if year.strip().isdigit() else None,
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    found = []
    for img in doc.select(".read-content img, .read-manga img"):
        src = picture(img, BASE)
        if src and "/images/" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
