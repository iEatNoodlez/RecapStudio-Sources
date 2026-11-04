"""MangaKatana: chapters from mangakatana.com.

Search, the Hot list (the site's "Hot Manga" box: one page), Latest updates and New manga; a series' page lists all
its chapters; a chapter's pictures are a list in the page's script (the one its reader puts into the pictures'
data-src).
"""
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests

RECAP_PLUGIN = 1
NAME = "MangaKatana"
VERSION = "1.0.0"
SITES = ["mangakatana.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "hot", "name": "Hot"}, {"id": "latest", "name": "Latest updates"}, {"id": "new", "name": "New manga"}]
HEADERS = {"Referer": "https://mangakatana.com/"}
DELAY = 0
TEST_QUERY = "solo leveling"

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

BASE = "https://mangakatana.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
FOLDER = {"latest": "latest", "new": "new-manga"}
WEBTOON = {"webtoon", "manhwa", "manhua"}  # (genres the site tags vertical comics with)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(3):
        r = _session.get(url, timeout=45)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        if not r.content.strip() and attempt < 2:  # (it sometimes answers with an empty page: asked again, it doesn't)
            time.sleep(2)
            continue
        if not r.content.strip():
            raise RuntimeError(f"{NAME} sent an empty page for {url}: try again in a minute")
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _kind(genres):
    names = {g.text.strip().lower() for g in genres}
    return "webtoon" if names & WEBTOON else "manga" if names else ""


def _cards(doc, where="#book_list > .item"):
    """Search and list results: [{"url", "title", "cover", "summary", "kind"}]."""
    out, seen = [], set()
    for card in doc.select(where):
        link = card.one("h3.title a, .title a")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen or "/manga/" not in url:
            continue
        seen.add(url)
        img = card.one("img")
        cover = absolute(BASE, img.get("lazy-src")) if img is not None and img.get("lazy-src") else picture(img, BASE)
        summary = card.one(".summary")
        out.append({"url": url, "title": link.text, "cover": cover, "summary": summary.text if summary is not None else "",
                    "kind": _kind(card.select(".genres a"))})
    return out


def _more(doc):
    return doc.one("a.next.page-numbers") is not None


def _listing(url):
    r = _get(url)
    doc = parse(r.text)
    if "/manga/" in r.url and doc.one("#single_book") is not None:
        # One match: the site goes straight to that series' page.
        s = _about(doc)
        return {"items": [{"url": r.url.split("?")[0], "title": s["title"], "cover": s["cover"],
                           "summary": s["summary"], "kind": s["kind"]}], "more": False}
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else "/"
    return _listing(f"{BASE}{folder}?search={quote_plus(query.strip())}&search_by=book_name")


def browse(list_id, page=1):
    if list_id == "hot":  # (a box beside the directory: one page only)
        if page > 1:
            return {"items": [], "more": False}
        return {"items": _cards(parse(_get(f"{BASE}/manga").text), "#hot_book .item"), "more": False}
    if list_id not in FOLDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    folder = f"/page/{page}" if page > 1 else ""
    return _listing(f"{BASE}/{FOLDER[list_id]}{folder}")


def _date(text):
    """"May-31-2023" as "2023-05-31" (left as it is when it's something else)."""
    text = (text or "").strip()
    try:
        return datetime.strptime(text, "%b-%d-%Y").date().isoformat()
    except ValueError:
        return text


def _about(doc):
    """A series' page: its title, cover, summary and kind."""
    title = doc.one("#single_book h1.heading, h1.heading")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}")
    summary = doc.one("#single_book .summary p, .summary p")
    return {"title": title.text, "cover": picture(doc.one("#single_book .cover img"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": _kind(doc.select("#single_book .genres a"))}


def series(url):
    # A series is /manga/<name>.<id>; a chapter's link (/manga/<name>.<id>/c12) is taken as its series.
    m = re.match(r"(https?://[^/]+/manga/[^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1)
    doc = parse(_get(url).text)
    out = {"url": url, **_about(doc)}
    chapters, seen = [], set()
    for row in doc.select(".chapters tr"):
        link = row.one(".chapter a")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        when = row.one(".update_time")
        chapters.append({"url": href, "name": link.text, "number": number(link.text),
                         "date": _date(when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    out["chapters"] = chapters
    return out


def pages(chapter_url):
    text = _get(chapter_url).text
    # The reader fills each picture's data-src from one of the script's lists (its name changes): find which.
    name = re.search(r"""data-src['"]\s*,\s*(\w+)""", text)
    found = []
    if name:
        listed = re.search(r"var\s+" + re.escape(name.group(1)) + r"\s*=\s*\[(.*?)\]", text, re.S)
        if listed:
            found = [absolute(chapter_url, u) for u in re.findall(r"""['"]([^'"]+)['"]""", listed.group(1))]
    if not found:  # (an older page: the pictures in the HTML)
        found = [picture(img, chapter_url) for img in parse(text).select("#imgs .wrap_img img")]
        found = [u for u in found if u and not u.endswith("#")]
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return list(dict.fromkeys(found))
