"""Omega Scans: chapters from omegascans.org, a site built on HeanCMS (a comic site system with its own JSON API, at api.<site>).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/heancms.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'Omega Scans'
VERSION = '1.0.0'
SITES = ['omegascans.org']
LANGUAGE = 'en'
NSFW = True
HEADERS = {'Referer': 'https://omegascans.org/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}]
DELAY = 0
BASE = 'https://omegascans.org'
OPTIONS = {'kind': 'webtoon'}

# engine: HeanCMS
# version: 1.0.0
# title: HeanCMS (a comic site system with its own JSON API, at api.<site>)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# Everything comes from the site's API: search and the Popular and Latest lists (/query), a series' details
# (/series/<slug>) and its chapters (/chapter/query), and a chapter's pictures (/chapter/<series>/<chapter>).
# Chapters the site still sells for its coins are left out until they're free (they then show up as new chapters).
# Novels (text, no pictures) are left out of search and lists.
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>; "kind", what kind of comic it has
# ("webtoon" for manhwa sites).
import time
from urllib.parse import quote, unquote, urlparse

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
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1)).rstrip("/")
KIND = OPTIONS.get("kind", "")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/", "Origin": BASE,
                         "Accept": "application/json, text/plain, */*"})
ORDER = {"popular": "total_views", "latest": "latest"}
PER_PAGE = 24


def _get(path, **params):
    """The API's answer (JSON) at API/<path>."""
    url = f"{API}/{path}"
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
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                               "the site may have changed or be down") from None
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _media(link):
    """A picture's full link (older versions of the API give ones relative to its own server)."""
    link = (link or "").strip()
    return link if not link or link.startswith(("http://", "https://")) else f"{API}/{link.lstrip('/')}"


def _plain(text):
    """A description as plain text (some are HTML)."""
    return parse(text).text if text and "<" in text else (text or "").strip()


def _novel(x):
    return str(x.get("series_type") or x.get("chapter_type") or "").lower() == "novel"


def _slugs(url):
    """The series' (and chapter's) slugs in a link: .../series/<series>[/<chapter>]."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if "series" in parts:
        parts = parts[parts.index("series") + 1:]
    if not parts:
        raise ValueError(f"{NAME}'s series links look like {BASE}/series/<name>, and {url} isn't one")
    return parts


def _list(page, query="", order="total_views"):
    """One page of the site's series (/query), as search and list results."""
    data = _get("query", query_string=query, series_status="All", order="desc", orderBy=order, series_type="Comic",
                page=page, perPage=PER_PAGE, tags_ids="[]")
    items = [{"url": f"{BASE}/series/{s['series_slug']}", "title": s.get("title") or s["series_slug"],
              "cover": _media(s.get("thumbnail")), "summary": _plain(s.get("description")), "kind": KIND}
             for s in data.get("data") or [] if s.get("series_slug") and not _novel(s)]
    meta = data.get("meta") or {}
    return {"items": items, "more": (meta.get("current_page") or page) < (meta.get("last_page") or 0)}


def search(query, page=1):
    return _list(page, query=query)


def browse(list_id, page=1):
    return _list(page, order=ORDER[list_id])


def series(url):
    slug = _slugs(url)[0]
    try:
        s = _get(f"series/{quote(slug, safe='')}")
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if _novel(s):
        raise RuntimeError(f"{NAME}: {s.get('title') or slug} is a novel (text, no pictures), which can't be recapped")
    chapters, page = [], 1
    while True:
        data = _get("chapter/query", series_id=s["id"], perPage=1000, page=page)
        for c in data.get("data") or []:
            if c.get("price") or not c.get("chapter_slug"):  # (still sold for the site's coins)
                continue
            name = c.get("chapter_name") or c["chapter_slug"]
            title = (c.get("chapter_title") or "").strip()
            folder = (c.get("series") or {}).get("series_slug") or s.get("series_slug") or slug
            chapters.append({"url": f"{BASE}/series/{folder}/{c['chapter_slug']}",
                             "name": f"{name} - {title}" if title and title not in name else name,
                             "number": number(name), "date": (c.get("created_at") or "")[:10]})
        meta = data.get("meta") or {}
        if not data.get("data") or page >= (meta.get("last_page") or 1):
            break
        page += 1
    year = str(s.get("release_year") or "")
    return {"url": f"{BASE}/series/{s.get('series_slug') or slug}", "title": s.get("title") or slug,
            "cover": _media(s.get("thumbnail")), "summary": _plain(s.get("description")), "kind": KIND,
            "year": int(year) if year.isdigit() else None,
            "chapters": list(reversed(chapters))}  # (newest first in the API)


def pages(chapter_url):
    parts = _slugs(chapter_url)
    if len(parts) < 2:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/series/<series>/<chapter>, and {chapter_url} isn't one")
    data = _get(f"chapter/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}")
    if data.get("paywall"):
        raise RuntimeError(f"{NAME} still sells this chapter for its coins: it can be got here once the site makes it free")
    chapter = data.get("chapter") or {}
    if _novel(chapter):
        raise RuntimeError(f"{NAME}: this chapter is text (a novel), not pictures")
    if isinstance(data.get("data"), list):  # (older versions of the API)
        found = data["data"]
    else:
        files = chapter.get("chapter_data") or {}
        found = files.get("images") or [f.get("url") for f in files.get("files") or [] if isinstance(f, dict)]
    out = []
    for link in found or []:
        link = _media(link if isinstance(link, str) else (link or {}).get("url"))
        if link and link not in out:
            out.append(link)
    if not out:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return out
