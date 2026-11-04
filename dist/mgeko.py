"""Mgeko: chapters from www.mgeko.cc (formerly MangaGeko).

Search is the site's /search/ page (30 a page); the lists come from its browser's own request (/browse-comics/data/,
JSON with the cards' HTML); a series' whole chapter list is /manga/<name>/all-chapters/; a chapter's pictures are on
its reader page.
"""
import re
import time
from urllib.parse import urlencode, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "Mgeko"
VERSION = "1.0.0"
SITES = ["mgeko.cc", "mangageko.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "weekly", "name": "Popular this week"},
         {"id": "latest", "name": "Latest updates"}, {"id": "new", "name": "Recently added"}]
HEADERS = {"Referer": "https://www.mgeko.cc/"}
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

BASE = "https://www.mgeko.cc"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SORT = {"popular": "popular_all_time", "weekly": "popular_weekly", "latest": "latest", "new": "recently_added"}
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
    """A link on the site as one on www.mgeko.cc (old mangageko.com links and ones without www)."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/")


def search(query, page=1):
    values = {"search": query.strip()}
    if page > 1:
        values["results"] = page  # (the site's own "Next" link: ?search=...&results=2)
    doc = parse(_get(f"{BASE}/search/?{urlencode(values)}").text)
    items, seen = [], set()
    for card in doc.select("li.novel-item"):
        link = card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        title = card.one(".novel-title")
        summary = card.one(".summary")
        items.append({"url": url, "title": link.get("title") or (title.text if title is not None else ""),
                      "cover": picture(card.one("img"), BASE),
                      "summary": (summary.get("title") or summary.text) if summary is not None else ""})
    more = any("next" in a.text.lower() for a in doc.select("nav.paging a"))
    return {"items": items, "more": bool(items) and more}


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    query = urlencode({"results": 1, "page": page, "sort": SORT[list_id]})
    found = _get(f"{BASE}/browse-comics/data/?{query}").json()
    doc = parse(found.get("results_html") or "")
    items, seen = [], set()
    for card in doc.select("article.comic-card"):
        link = card.one(".comic-card__title a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        summary = card.one(".comic-card__description")
        summary = summary.text if summary is not None else ""
        img = card.one("img")
        items.append({"url": url, "title": link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "summary": "" if summary == "Not Provided." else summary})
    return {"items": items, "more": bool(items) and page < int(found.get("num_pages") or 0)}


def _date(text):
    """Django's "July 13, 2024, 5:46 a.m." / "Jan. 1, 2022, ..." as "2024-07-13" (else as it is)."""
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})", text or "")
    if m and m.group(1)[:3].lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
    return (text or "").strip()


def _chapter_name(title):
    """The site's chapter titles are slugs: "179-5-eng-li" is "Chapter 179.5", "200-side-story-21-eng-li"
    "Chapter 200 - side story 21", "spoiler-eng-li" "Spoiler". Gives (name, number)."""
    title = title.strip()
    lang = re.search(r"-([a-z]+)-li$", title)
    base = title[:lang.start()] if lang else title
    extra = f" ({lang.group(1)})" if lang and lang.group(1) != "eng" else ""
    m = re.match(r"(\d+)(?:[.-](\d+))?(?:-(.+))?$", base)
    if not m:
        return base.replace("-", " ").strip().capitalize() + extra, None
    num = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
    name = f"Chapter {num}" + (f" - {m.group(3).replace('-', ' ')}" if m.group(3) else "") + extra
    return name, number(name)


def series(url):
    url = _on_site(url)
    if "/reader/" in url:  # (a chapter's link: its page links to its series)
        link = parse(_get(url).text).one("a[href^='/manga/'], a[href*='mgeko.cc/manga/']")
        if link is None:
            raise ValueError(f"That isn't a series' link on {NAME}: {url}")
        url = absolute(BASE, link.get("href"))
    m = re.match(r"(https://[^/]+/manga/[^/?#]+)", url)
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1) + "/"
    doc = parse(_get(url).text)
    title = doc.one(".main-head h1, h1.novel-title")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    about = doc.one("p.description")
    summary = about.text if about is not None else ""
    if "The Summary is" in summary:  # (the site's own sentence about itself comes first)
        summary = summary.split("The Summary is", 1)[1].strip()
    genres = {a.text.strip().lower() for a in doc.select(".categories a")}
    kind = ("webtoon" if genres & {"webtoon", "webtoons", "manhwa", "manhua"} else
            "manga" if "manga" in genres else "")

    rows = parse(_get(url + "all-chapters/").text).select("ul.chapter-list li")
    chapters, seen = [], set()
    for row in rows:
        link = row.one("a[href]")
        label = row.one(".chapter-title")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name, num = _chapter_name(label.text if label is not None else link.get("title"))
        when = row.one("time")
        chapters.append({"url": href, "name": name, "number": num,
                         "date": _date(when.get("datetime")) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one("figure.cover img"), BASE),
            "summary": summary, "kind": kind, "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(_on_site(chapter_url)).text)
    found = []
    for img in doc.select("section.page-in img[onerror], section.page-in img.mgr-page"):
        src = picture(img, BASE)
        if src and "credits-mgeko" not in src and "/static/" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
