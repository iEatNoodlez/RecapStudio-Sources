"""MangaTown: chapters from www.mangatown.com.

Search is /search?name=…&page=…; the lists are the site's Hot Manga, Latest Releases and its directory sorted by
rating (/hot/, /latest/, /directory/?rating.za; 30 a page); a series' whole chapter list is on its page (newest
first); and a chapter's reader shows one picture a page, so pages() reads the chapter's page list and visits every
page, a few at a time, for its picture. The pictures want the site as Referer.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "MangaTown"
VERSION = "1.0.0"
SITES = ["mangatown.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://www.mangatown.com/"}
DELAY = 0
TEST_QUERY = "golden kamui"

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

BASE = "https://www.mangatown.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PAGE_READERS = 3  # how many of a chapter's pages are fetched at a time
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon"}
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


def _on_site(url):
    """A link on the site as one on www.mangatown.com (http ones, the mobile site's, ones without www)."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _results(url):
    """The series on a page of cards (search, Hot, Latest, the directory): {"items", "more"}."""
    doc = parse(_get(url).text)
    items, seen = [], set()
    for card in doc.select("ul.manga_pic_list li"):
        link = card.one("p.title a, a.manga_cover")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        img = card.one("a.manga_cover img")
        items.append({"url": href, "title": link.get("title") or link.text,
                      "cover": picture(img, BASE)})
    nxt = doc.one("a.next")
    more = nxt is not None and nxt.get("href").startswith("/")  # (javascript:void(0) on the last page)
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    return _results(f"{BASE}/search?{urlencode({'name': query.strip(), 'page': page})}")


def browse(list_id, page=1):
    suffix = f"{page}.htm" if page > 1 else ""
    if list_id == "popular":
        return _results(f"{BASE}/hot/{suffix}")
    if list_id == "latest":
        return _results(f"{BASE}/latest/{suffix}")
    if list_id == "rating":
        return _results(f"{BASE}/directory/{suffix}?rating.za")
    raise ValueError(f"{NAME} has no list {list_id!r}")


def _date(text):
    """"Sep 02,2026" as "2026-09-02" (else as it is: "Today", "Yesterday")."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", text or "")
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return (text or "").strip()


def _detail(doc, label):
    """The value after a "Type:" / "Status(s):" label on a series' page."""
    for b in doc.select(".detail_info li b"):
        if b.text.rstrip(": ").lower().startswith(label) and b.parent is not None:
            value = b.parent.one("a")
            return value.text if value is not None else b.parent.own_text
    return ""


def series(url):
    url = _on_site(url)
    m = re.match(r"(https://[^/]+/manga/[^/?#]+)", url)
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1) + "/"  # (a chapter's link, /manga/<name>/c001/, is its series' plus the chapter)
    doc = parse(_get(url).text)
    title = doc.one("h1.title-top")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    img, meta = doc.one(".detail_info img"), doc.one("meta[property='og:image']")
    cover = picture(img, BASE) if img is not None else absolute(BASE, meta.get("content")) if meta is not None else ""
    about = doc.one("span#show") or doc.one("span#hide")  # (the whole summary, else the shortened one shown)
    chapters, seen = [], set()
    for row in doc.select("ul.chapter_list li"):
        link = row.one("a[href]")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        m = re.search(r"/c(\d+(?:\.\d+)?)/?$", href)
        num = float(m.group(1)) if m else number(link.text)
        extra = " - ".join(s.text for s in row.select("span") if s.text and "time" not in s.classes and "new" not in s.classes)
        name = (f"Chapter {m.group(1).lstrip('0') or '0'}" if m else link.text) + (f" - {extra}" if extra else "")
        when = row.one("span.time")
        chapters.append({"url": href, "name": name, "number": num, "date": _date(when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text, "cover": cover,
            "summary": about.own_text if about is not None else "",
            "kind": KINDS.get(_detail(doc, "type").strip().lower(), ""), "chapters": chapters}


def _picture(doc):
    img = doc.one("img#image")
    return picture(img, BASE) if img is not None else ""


def pages(chapter_url):
    url = _on_site(chapter_url)
    if not re.search(r"/manga/[^/]+/(?:v[^/]+/)?c[^/]+/?", url):
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    doc = parse(_get(url).text)
    first = _picture(doc)
    if not first:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    # The reader shows one picture a page; the page list is the reader's own dropdown ("featured" is an ad page).
    links = []
    for option in doc.select(".page_select select option"):
        href = absolute(BASE, option.get("value"))
        if href and href not in links and "featured" not in href.rsplit("/", 1)[-1]:
            links.append(href)
    rest = links[1:] if links else []
    with ThreadPoolExecutor(PAGE_READERS) as pool:
        found = [first] + list(pool.map(lambda href: _picture(parse(_get(href).text)), rest))
    pictures = []
    for src in found:
        if src and src not in pictures:
            pictures.append(src)
    return pictures
