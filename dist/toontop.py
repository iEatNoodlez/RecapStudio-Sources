"""ToonTop: chapters from toontop.io, a site built on the Comizy reader (Comizy, TOONTOP: a Next.js site with a JSON API at api.<site>).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/comizy.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'ToonTop'
VERSION = '1.0.0'
SITES = ['toontop.io']
LANGUAGE = 'en'
NSFW = True
HEADERS = {'Referer': 'https://toontop.io/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}, {'id': 'new', 'name': 'Recently added'}]
DELAY = 0
BASE = 'https://toontop.io'
OPTIONS = {'kind': 'webtoon'}

# engine: Comizy
# version: 1.0.0
# title: the Comizy reader (Comizy, TOONTOP: a Next.js site with a JSON API at api.<site>)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}, {"id": "new", "name": "Recently added"}]
#
# Search and the lists come from the site's API (/titles/search, sorted by views, latest chapter or newest series);
# a series' details from /titles/by-slug/<name> and its whole chapter list from /titles/<id>/chapters; a chapter's
# pictures from /titles/by-slug/<name>/chapters/<chapter> (the same JSON the site's own pages are rendered from).
# The pictures need the site as Referer (HEADERS has it).
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>; "kind", the kind of every series when the
# API doesn't say ("webtoon" for an all-manhwa site).
import re
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
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1)).rstrip("/")
SORT = {"popular": "views", "latest": "latest", "new": "newest"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
NOT_SERIES = {"search", "latest", "popular", "home", "genres", "genre", "browse", "ranking", "lists", "filters", "auth",
              "about", "dmca", "contact", "terms", "static", "api", "_next"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/", "Accept": "application/json"})


def _api(path, **params):
    """The API's answer (its "data") at API/<path>."""
    for attempt in range(2):
        r = _session.get(f"{API}/{path}", params=params, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After") or "10"
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        try:
            data = r.json()
        except ValueError:
            r.raise_for_status()
            raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                               "the site may have changed or be down") from None
        if r.status_code == 404 or data.get("code") == "NOT_FOUND":
            raise ValueError(f"{NAME} has no such page: {data.get('message') or r.url}")
        if r.status_code >= 400 or data.get("success") is False:
            raise RuntimeError(f"{NAME}'s API answered: {data.get('message') or r.status_code}")
        return data.get("data") or {}
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _plain(text):
    """A summary as plain text (they carry a little Markdown: links, bold)."""
    text = (text or "").replace("\r", "")
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _kind(item):
    name = ((item.get("type") or {}).get("name") or "").lower()
    return KINDS.get(name, "") or OPTIONS.get("kind", "")


def _card(item):
    year = str(item.get("release_date") or "")
    return {"url": BASE + (item.get("url") or f"/{item.get('slug', '')}"),
            "title": (item.get("name") or item.get("slug") or "").strip(), "cover": item.get("cover") or "",
            "summary": _plain(item.get("summary")), "kind": _kind(item),
            "year": int(year) if len(year) == 4 and year.isdigit() else None}


def _list(page, **params):
    """One page of the site's series (/titles/search), as search and list results."""
    data = _api("titles/search", page=page, **params)
    items = [_card(i) for i in data.get("items") or [] if i.get("url") or i.get("slug")]
    return {"items": items, "more": bool(items) and bool((data.get("pagination") or {}).get("has_next"))}


def search(query, page=1):
    return _list(page, q=query.strip())


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _list(page, sort=SORT[list_id])


def _slugs(url):
    """The series' (and chapter's) names in a link: /<series>[/<chapter>], or the API's /titles/<id>-<series>."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if parts and parts[0] == "titles":
        parts = [p.split("-", 1)[1] if "-" in p else p for p in parts[1:]]
    if not parts or parts[0] in NOT_SERIES:
        raise ValueError(f"{NAME}'s series links look like {BASE}/<name>, and {url} isn't one")
    return parts


def series(url):
    slug = _slugs(url)[0]
    try:
        title = _api(f"titles/by-slug/{slug}", include="details").get("title") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if not title.get("id"):
        raise ValueError(f"{NAME} has no series at {url}")
    # The whole list comes at once, newest first.
    rows = _api(f"titles/{title['id']}/chapters", limit=500).get("chapters") or []
    chapters, seen = [], set()
    for c in rows:
        link = c.get("url") or (f"/{title.get('slug') or slug}/{c['slug']}" if c.get("slug") else "")
        if not link or link in seen:
            continue
        seen.add(link)
        name = (c.get("name") or c.get("slug") or "").strip() or "Chapter"
        chapters.append({"url": BASE + link, "name": name, "number": number(name),
                         "date": (c.get("updated_at") or "")[:10]})
    chapters.reverse()
    return {**_card(title), "chapters": chapters}


def pages(chapter_url):
    parts = _slugs(chapter_url)
    if len(parts) < 2:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/<series>/<chapter>, and {chapter_url} isn't one")
    try:
        chapter = _api(f"titles/by-slug/{parts[0]}/chapters/{parts[1]}", include="details").get("chapter") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no chapter at {chapter_url}") from None
    found = []
    for item in chapter.get("images") or [p.get("url") for p in chapter.get("pages") or [] if isinstance(p, dict)]:
        if isinstance(item, str) and item.strip() and item not in found:
            found.append(item.strip())
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
