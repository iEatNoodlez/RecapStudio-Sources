"""Vortex Scans: chapters from vortexscans.org, a site built on VTheme (V DEV's comic site system, with a JSON API at api.<site>/api).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/vtheme.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'Vortex Scans'
VERSION = '1.0.0'
SITES = ['vortexscans.org']
LANGUAGE = 'en'
NSFW = False
HEADERS = {'Referer': 'https://vortexscans.org/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}]
DELAY = 0
BASE = 'https://vortexscans.org'
OPTIONS = {}

# engine: VTheme
# version: 1.0.0
# title: VTheme (V DEV's comic site system, with a JSON API at api.<site>/api)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# Search, the Popular and Latest lists, a series' details and its chapters come from the site's API (/query, /post,
# /chapters); a chapter's pictures from its page (the reader's pictures are in the page's HTML).
# Chapters the site still sells for its coins are left out until they're free (they then show up as new chapters).
# Novels (text, no pictures) are left out of search and lists.
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>/api.
import time
from urllib.parse import unquote, urlparse

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
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1) + "/api").rstrip("/")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "totalViews", "latest": "lastChapterAddedAt"}
KINDS = {"MANHWA": "webtoon", "MANHUA": "webtoon", "MANGA": "manga", "COMIC": "comic"}
PER_PAGE = 24


def _get(url, **params):
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


def _api(path, **params):
    """The API's answer (JSON) at API/<path>."""
    r = _get(f"{API}/{path}", **params)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                           "the site may have changed or be down") from None


def _plain(text):
    """A description as plain text (they're HTML)."""
    return parse(text).text if text and "<" in text else (text or "").strip()


def _novel(post):
    return post.get("isNovel") or str(post.get("seriesType") or "").upper() == "NOVEL"


def _slugs(url):
    """The series' (and chapter's) slugs in a link: .../series/<series>[/<chapter>]."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if "series" in parts:
        parts = parts[parts.index("series") + 1:]
    if not parts:
        raise ValueError(f"{NAME}'s series links look like {BASE}/series/<name>, and {url} isn't one")
    return parts


def _list(page, query="", order="totalViews"):
    """One page of the site's series (/query), as search and list results."""
    params = {"page": page, "perPage": PER_PAGE, "orderBy": order, "orderDirection": "desc"}
    if query:
        params["searchTerm"] = query
    data = _api("query", **params)
    items = [{"url": f"{BASE}/series/{p['slug']}", "title": (p.get("postTitle") or p["slug"]).strip(),
              "cover": p.get("featuredImage") or "", "kind": KINDS.get(str(p.get("seriesType") or "").upper(), "")}
             for p in data.get("posts") or [] if p.get("slug") and not _novel(p)]
    return {"items": items, "more": page * PER_PAGE < (data.get("totalCount") or 0)}


def search(query, page=1):
    return _list(page, query=query)


def browse(list_id, page=1):
    return _list(page, order=ORDER[list_id])


def series(url):
    slug = _slugs(url)[0]
    try:
        post = _api("post", postSlug=slug).get("post") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if not post.get("id"):
        raise ValueError(f"{NAME} has no series at {url}")
    if _novel(post):
        raise RuntimeError(f"{NAME}: {post.get('postTitle') or slug} is a novel (text, no pictures), which can't be recapped")
    chapters, skip, take = [], 0, 500
    while True:
        data = _api("chapters", postId=post["id"], take=take, skip=skip)
        got = (data.get("post") or {}).get("chapters") or []
        for c in got:
            if not c.get("slug") or c.get("isAccessible") is False or (c.get("isLocked") and not c.get("chapterPurchased")):
                continue  # (still sold for the site's coins)
            n = c.get("number")
            title = (c.get("title") or "").strip()
            name = (f"Chapter {n:g}" if isinstance(n, (int, float)) else c["slug"].replace("-", " ").title())
            folder = (c.get("mangaPost") or {}).get("slug") or post.get("slug") or slug
            chapters.append({"url": f"{BASE}/series/{folder}/{c['slug']}", "name": f"{name} - {title}" if title else name,
                             "number": float(n) if isinstance(n, (int, float)) else number(c["slug"]),
                             "date": (c.get("createdAt") or "")[:10]})
        skip += take
        if len(got) < take or skip >= (data.get("totalChapterCount") or 0):
            break
    return {"url": f"{BASE}/series/{post.get('slug') or slug}", "title": (post.get("postTitle") or slug).strip(),
            "cover": post.get("featuredImage") or "", "summary": _plain(post.get("postContent")),
            "kind": KINDS.get(str(post.get("seriesType") or "").upper(), ""),
            "chapters": list(reversed(chapters))}  # (newest first in the API)


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    imgs = doc.select("img[data-reader-page-image]")
    if imgs:  # (in reading order by their index)
        imgs.sort(key=lambda img: int(img.get("data-reader-index", "0")) if img.get("data-reader-index", "0").isdigit() else 0)
    else:  # (older versions of the theme)
        imgs = [img for img in doc.select("section[itemprop=articleBody] figure img, .image-container img[data-image-index]")
                if not img.get("itemprop")]
    found = []
    for img in imgs:
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        if "Cost to Unlock" in doc.text or doc.one("a[href*='/auth/signin']") is not None and "unlock" in doc.text.lower():
            raise RuntimeError(f"{NAME} still sells this chapter for its coins: it can be got here once the site makes it free")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
