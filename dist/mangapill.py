"""Mangapill: chapters from mangapill.com.

Everything is on the site's own pages: search at /search?q=…&page=…, a series' whole chapter list on its page
(/manga/<id>/<name>, newest first), and a chapter's pictures on its reader page (/chapters/<id>-<number>/<name>),
lazy-loaded (data-src) from the site's CDN, which wants the site as Referer. The lists are the home page's Trending
and the New chapters page (/chapters), one page each. The site dropped manhwa in 2024: it has manga only.
"""
import re
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "Mangapill"
VERSION = "1.0.0"
SITES = ["mangapill.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "trending", "name": "Trending"}, {"id": "latest", "name": "New chapters"}]
HEADERS = {"Referer": "https://mangapill.com/"}
DELAY = 0
TEST_QUERY = "one piece"

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

BASE = "https://mangapill.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon", "oel": "comic"}  # (its other types: novel, one-shot...)
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


def _series_link(href):
    """A series' link (/manga/<id>/<name>) as a full one, or "" for any other link."""
    m = re.match(r"/manga/(\d+)(?:/([^/?#]+))?", href or "")
    return f"{BASE}/manga/{m.group(1)}" + (f"/{m.group(2)}" if m.group(2) else "") if m else ""


def _cards(root):
    """The series shown in a part of a page (search results, the home page's Trending, the New chapters page):
    each card is the nearest box around a series' link that holds a picture."""
    items, seen = [], set()
    for link in root.select("a[href^='/manga/']"):
        url = _series_link(link.get("href"))
        if not url or url in seen:
            continue
        card = link.parent if link.parent is not None else link
        for _ in range(3):  # (the series' link is the cover's, or sits a level or two inside the card)
            if card.parent is None or card.one("img") is not None:
                break
            card = card.parent
        img = card.one("img")
        heading = link.one("div") or card.one(".font-black")
        title = heading.text if heading is not None else link.text
        if not title and img is not None:
            title = img.get("alt")
        if not title:
            continue
        seen.add(url)
        badges = [b.text.strip().lower() for b in card.select(".text-xs.leading-5")]
        year = next((b for b in badges if re.fullmatch(r"\d{4}", b)), "")
        items.append({"url": url, "title": title, "cover": picture(img, BASE),
                      "kind": next((KINDS[b] for b in badges if b in KINDS), ""),
                      "year": int(year) if year else None})
    return items


def search(query, page=1):
    doc = parse(_get(f"{BASE}/search?{urlencode({'q': query.strip(), 'page': page})}").text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and doc.one(f"a[href*='page={page + 1}']") is not None}


def browse(list_id, page=1):
    if list_id == "trending":
        if page > 1:
            return {"items": [], "more": False}
        doc = parse(_get(BASE + "/").text)
        section = next((h for h in doc.select("h4") if "trending" in h.text.lower()), None)
        if section is None:
            raise RuntimeError(f"{NAME}'s home page shows no Trending list right now")
        root = section.parent.parent if section.parent is not None and section.parent.parent is not None else doc
        return {"items": _cards(root), "more": False}
    if list_id == "latest":
        if page > 1:  # (the page takes no page number: it shows the newest chapters, one page)
            return {"items": [], "more": False}
        return {"items": _cards(parse(_get(f"{BASE}/chapters").text)), "more": False}
    raise ValueError(f"{NAME} has no list {list_id!r}")


def _detail(doc, label):
    """The value under a "Type" / "Status" / "Year" label on a series' page."""
    for tag in doc.select("label"):
        if tag.text.strip().lower() == label and tag.parent is not None:
            value = tag.parent.one("div")
            return value.text if value is not None else ""
    return ""


def series(url):
    path = re.sub(r"^https?://[^/]+", "", url.strip())
    page_url = _series_link(path)
    if not page_url:
        m = re.match(r"/chapters/(\d+)-\d+", path)  # (a chapter's link: its series' id comes first)
        if not m:
            raise ValueError(f"That isn't a series' link on {NAME}: {url}")
        page_url = f"{BASE}/manga/{m.group(1)}"
    doc = parse(_get(page_url).text)
    title = doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one("p.text--secondary")
    year = _detail(doc, "year")
    rows = doc.select("#chapters a[href^='/chapters/']")
    chapters, seen = [], set()
    for row in rows:
        link = absolute(BASE, row.get("href"))
        if link in seen:
            continue
        seen.add(link)
        name = row.text or row.get("title").strip() or "Chapter"
        num = number(name)
        if num is None:  # (the link's own number: /chapters/<series>-1<number * 1000>/...)
            m = re.search(r"/chapters/\d+-1(\d{4,})(?:/|$)", link)
            num = int(m.group(1)) / 1000 if m else None
        chapters.append({"url": link, "name": name, "number": num, "date": ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": page_url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else "",
            "summary": summary.text if summary is not None else "",
            "kind": KINDS.get(_detail(doc, "type").strip().lower(), ""),
            "year": int(year) if year.strip().isdigit() else None, "chapters": chapters}


def pages(chapter_url):
    path = re.sub(r"^https?://[^/]+", "", chapter_url.strip())
    if not path.startswith("/chapters/"):
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    doc = parse(_get(BASE + path).text)
    found = []
    for img in doc.select("img.js-page"):
        src = picture(img, BASE)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
