"""Demonic Scans: chapters from demonicscans.org (its own translations and others', manhwa, manhua and manga).

Search is the site's own search request (/search.php?manga=…, the cards its search box shows, one page); the lists
are its Latest Updates (/lastupdates.php), Top Views (/advanced.php) and New Manga (/newmangalist.php) pages; a
series' chapters are all on its page (/manga/<Title>), linked as /chaptered.php?manga=<id>&chapter=<n>, which the
site sends on to /title/<Title>/chapter/<n>/1; a chapter's pictures are on that page. The site's novels are left out.
"""
import re
import time
from urllib.parse import urlparse

import requests
from requests.utils import requote_uri

RECAP_PLUGIN = 1
NAME = "Demonic Scans"
VERSION = "1.0.0"
SITES = ["demonicscans.org"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "latest", "name": "Latest updates"}, {"id": "popular", "name": "Top views"},
         {"id": "new", "name": "New titles"}]
HEADERS = {"Referer": "https://demonicscans.org/"}
DELAY = 0
TEST_QUERY = "solo"

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

BASE = "https://demonicscans.org"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PAGES = {"latest": "lastupdates.php", "popular": "advanced.php", "new": "newmangalist.php"}
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "webtoons": "webtoon"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url, params=None):
    for attempt in range(2):
        r = _session.get(url, params=params, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After") or "10"
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {r.url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _picture(img):
    """An <img>'s picture as a link that can be fetched (the site's have spaces in them)."""
    src = picture(img, BASE)
    return requote_uri(src) if src else ""


def search(query, page=1):
    if page > 1:  # (the site's search box shows every match at once)
        return {"items": [], "more": False}
    doc = parse(_get(f"{BASE}/search.php", {"manga": query.strip()}).text)
    items, seen = [], set()
    for link in doc.select("a[href*='/manga/']"):
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        name = link.one(".seach-right > div") or link.one("div")
        title = (name.text if name is not None else link.text).strip()
        if title:
            items.append({"url": url, "title": title, "cover": _picture(link.one("img"))})
    return {"items": items, "more": False}


def browse(list_id, page=1):
    if list_id not in PAGES:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    doc = parse(_get(f"{BASE}/{PAGES[list_id]}", {"list": page} if page > 1 else None).text)
    items, seen = [], set()
    for card in doc.select(".updates-element, .advanced-element"):
        link = card.one("h2 a[href*='/manga/']") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one("img")
        title = link.get("title") or (img.get("title") if img is not None else "") or link.text
        items.append({"url": url, "title": title.strip(), "cover": _picture(img)})
    more = any(a.text.strip().lower() == "next" for a in doc.select(".pagination a"))
    return {"items": items, "more": bool(items) and more}


def _series_url(url):
    """The series' page (/manga/<Title>) from any of its links: /manga/…, /title/<Title>/chapter/…, or
    /chaptered.php?manga=<id>&chapter=<n> (which the site sends on to a /title/ link)."""
    url = absolute(BASE, url.strip())
    m = re.match(r"/(?:manga|title)/([^/?#]+)", urlparse(url).path)
    if m is None and "chaptered.php" in url:
        m = re.match(r"/(?:manga|title)/([^/?#]+)", urlparse(_get(url).url).path)
    if m is None:
        raise ValueError(f"{NAME}'s series links look like {BASE}/manga/<Title>, and {url} isn't one")
    return f"{BASE}/manga/{m.group(1)}"


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1.big-fat-titles") or doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("#manga-page img")
    if cover is None:
        cover = doc.one("meta[property='og:image']")
        cover_url = requote_uri(absolute(BASE, cover.get("content"))) if cover is not None else ""
    else:
        cover_url = _picture(cover)
    about = doc.one(".white-font")
    summary = about.text if about is not None else ""
    kind = ""
    if about is not None:  # ("…, Manhua series, english chapters have been translated…")
        m = re.search(r"\b(Manga|Manhwa|Manhua|Webtoons?)\s+series\b", about.text, re.I)
        kind = KINDS.get(m.group(1).lower(), "") if m else ""
    if "The Summary is" in summary:  # (the site's own sentence about itself comes first)
        summary = summary.split("The Summary is", 1)[1].strip()
    if not kind:
        genres = {li.text.strip().lower() for li in doc.select(".genres-list li")}
        kind = next((KINDS[g] for g in ("webtoons", "webtoon", "manhwa", "manhua", "manga") if g in genres), "")
    chapters, seen = [], set()
    rows = doc.select("#chapters-list a.chplinks") or doc.select("#chapters-list a[href*='chaptered.php']")
    for row in rows:  # (newest first)
        href = absolute(BASE, row.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = row.own_text or (row.get("title") or "").strip() or "Chapter"
        when = row.one("span")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": when.text.strip() if when is not None else ""})
    chapters.reverse()
    return {"url": url, "title": title.text, "cover": cover_url, "summary": summary, "kind": kind, "chapters": chapters}


def pages(chapter_url):
    url = absolute(BASE, chapter_url.strip())
    if "chaptered.php" not in url and "/title/" not in url:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/chaptered.php?manga=<id>&chapter=<n>, and "
                         f"{chapter_url} isn't one")
    doc = parse(_get(url).text)
    imgs = doc.select("img.imgholder[onerror]") or doc.select("img.imgholder")
    found = []
    for img in imgs:
        src = _picture(img)
        if src and not src.startswith(BASE + "/img/") and src not in found:  # (the site's own banners)
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
