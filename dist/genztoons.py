"""Genz Toon: chapters from genztoons.org (manhwa, with a few manga and comics; its novels are left out).

Search filters the site's own list of every series (/search_series, the cards its search panel shows) by title; the
lists are the home page's Latest Updates, Trending and Recently Added sections; a series' chapters are all on its
page (/series/<name>/), the ones still sold for the site's coins (shown with a lock) left out; a chapter's pictures
are the reader's lazy-loaded images, each named by its uid on the site's CDN (cdn.meowing.org/uploads/<uid>).
"""
import re
import time
from datetime import date, timedelta
from urllib.parse import urlparse

import requests

RECAP_PLUGIN = 1
NAME = "Genz Toon"
VERSION = "1.0.0"
SITES = ["genztoons.org"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "latest", "name": "Latest updates"}, {"id": "trending", "name": "Trending"},
         {"id": "new", "name": "Recently added"}]
HEADERS = {"Referer": "https://genztoons.org/"}
DELAY = 0
TEST_QUERY = "disciple"

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

BASE = "https://genztoons.org"
CDN = "https://cdn.meowing.org/uploads/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "mangatoon": "webtoon", "manga": "manga", "comic": "comic"}
SECTIONS = {"trending": "trending", "new": "recently added"}  # (the home page's headings)
PER_PAGE = 30
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After") or "10"
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _cover(node):
    """The picture in a node's background-image style."""
    m = re.search(r"url\(\s*['\"]?([^'\")]+)", node.get("style")) if node is not None else None
    return absolute(BASE, m.group(1)) if m else ""


def _button_card(btn):
    """A series card (<button data-type><a href="/series/…">…) as an item, or None for a novel."""
    kind = btn.get("data-type").strip().lower()
    link = btn.one("a[href*='/series/']")
    if link is None or kind == "novel":
        return None
    return {"url": absolute(BASE, link.get("href")),
            "title": (btn.get("alt") or link.get("alt") or link.get("title") or link.text).strip(),
            "cover": _cover(link.one("div[style*='background-image']")), "kind": KINDS.get(kind, "")}


def search(query, page=1):
    """Every series whose title has the words typed (the site's own search filters its full list the same way)."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    doc = parse(_get(f"{BASE}/search_series").text)
    items, seen = [], set()
    for btn in doc.select("button[data-type]"):
        text = f"{btn.get('alt')} {btn.get('title')}".lower()
        if not all(w in text for w in words):
            continue
        card = _button_card(btn)
        if card is None or card["url"] in seen:
            continue
        seen.add(card["url"])
        items.append(card)
    start = (page - 1) * PER_PAGE
    return {"items": items[start:start + PER_PAGE], "more": len(items) > start + PER_PAGE}


def browse(list_id, page=1):
    if list_id != "latest" and list_id not in SECTIONS:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    if page > 1:  # (the home page's sections have one page)
        return {"items": [], "more": False}
    doc = parse(_get(BASE + "/").text)
    items, seen = [], set()
    if list_id == "latest":
        for card in doc.select("#latest .latest-poster"):
            link = card.one("a[href*='/series/']")
            if link is None:
                continue
            url = absolute(BASE, link.get("href"))
            if url in seen:
                continue
            seen.add(url)
            title = card.one("h3")
            badge = card.one("span.capitalize")
            items.append({"url": url, "title": (title.text if title is not None else link.get("title")).strip(),
                          "cover": _cover(link), "kind": KINDS.get(badge.text.lower(), "") if badge is not None else ""})
    else:
        heading = next((h for h in doc.select("h2") if h.text.lower() == SECTIONS[list_id]), None)
        if heading is None or heading.parent is None or heading.parent.parent is None:
            raise RuntimeError(f"{NAME}'s home page has no “{SECTIONS[list_id].title()}” section any more")
        for btn in heading.parent.parent.select("button[data-type]"):
            card = _button_card(btn)
            if card is not None and card["url"] not in seen:
                seen.add(card["url"])
                items.append(card)
    return {"items": items, "more": False}


def _date(text):
    """The site's "Oct 26, 2026" as "2026-10-26"; "5 days ago" counted back from today; else as it is."""
    text = (text or "").strip()
    m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})$", text)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$", text, re.I)
    if m:
        days = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}[m.group(2).lower()]
        return (date.today() - timedelta(days=days * int(m.group(1)))).isoformat()
    if text.lower() in ("just now", "today"):
        return date.today().isoformat()
    if text.lower() == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    return text


def _series_url(url):
    """The series' page from its link, or from one of its chapters' (the reader links back to it)."""
    path = urlparse(url.strip()).path
    if "/chapter/" in path:
        for a in parse(_get(absolute(BASE, path)).text).select("a[href*='/series/']"):
            m = re.match(r"/series/([^/?#]+)", urlparse(absolute(BASE, a.get("href"))).path)
            if m:
                return f"{BASE}/series/{m.group(1)}/"
    m = re.match(r"/series/([^/?#]+)", path)
    if not m:
        raise ValueError(f"{NAME}'s series links look like {BASE}/series/<name>/, and {url} isn't one")
    return f"{BASE}/series/{m.group(1)}/"


def _kind(doc):
    """The "Type" field on a series' page (manhwa, manga, comic, novel)."""
    for label in doc.select("span"):
        if label.text == "Type" and label.parent is not None and label.parent.parent is not None:
            value = label.parent.parent.one("div.uppercase")
            if value is not None:
                return value.text.strip().lower()
    return ""


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    kind = _kind(doc)
    if kind == "novel":
        raise RuntimeError(f"{NAME}: {title.text} is a novel (text, no pictures), which can't be recapped")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one("#expand_content p")
    chapters, seen = [], set()
    for row in doc.select("#chapters a[href*='/chapter/']"):  # (newest first)
        lock = row.one("img[src*='lock']")
        if lock is not None and "lock-open" not in lock.get("src"):
            continue  # (still sold for the site's coins: it shows up once the site makes it free)
        href = absolute(BASE, row.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = (row.get("title") or row.get("alt") or row.text).strip() or "Chapter"
        chapters.append({"url": href, "name": name, "number": number(name), "date": _date(row.get("d"))})
    chapters.reverse()
    return {"url": url, "title": title.text, "cover": absolute(BASE, cover.get("content")) if cover is not None else "",
            "summary": summary.text if summary is not None else "", "kind": KINDS.get(kind, ""), "chapters": chapters}


def pages(chapter_url):
    path = urlparse(chapter_url.strip()).path
    if "/chapter/" not in path:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/chapter/<id>/, and {chapter_url} isn't one")
    html = _get(absolute(BASE, path)).text
    doc = parse(html)
    # The reader's loadImage() says where the pictures are: https://cdn.<host>/uploads/${uid}.
    m = re.search(r"(https://cdn\d?\.[\w.-]+/uploads/)\$\{uid\}", html)
    cdn = m.group(1) if m else CDN
    imgs = [img for img in doc.select("img.myImage[uid]") if img.get("uid").strip()]
    imgs.sort(key=lambda img: int(img.get("count")) if img.get("count").isdigit() else 0)
    found = []
    for img in imgs:
        src = cdn + img.get("uid").strip()
        if src not in found:
            found.append(src)
    if not found:
        if re.search(r"unlock|purchase|coins", doc.text, re.I):
            raise RuntimeError(f"{NAME} still sells this chapter for its coins: it can be got here once the site makes it free")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
