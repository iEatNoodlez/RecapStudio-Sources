"""MangaFreak: chapters from mangafreak.me (ww3.mangafreak.me and its other ww* mirrors).

Search is /Find/<words>?page=…; the lists are its ranking (/Genre/All/<page>, 15 a page) and Latest Releases
(/Latest_Releases/<page>, 30 a page); a series' whole chapter list is on its page (/Manga/<Name>, first chapter
first); and a chapter's pictures are all on its reader page (/Read1_<Name>_<n>), from images.mangafreak.me, which
wants the site as Referer.
"""
import re
import time
from urllib.parse import quote, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "MangaFreak"
VERSION = "1.0.0"
SITES = ["mangafreak.me"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest releases"}]
HEADERS = {"Referer": "https://ww3.mangafreak.me/"}
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

BASE = "https://ww3.mangafreak.me"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
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
    """A link on any of the site's mirrors (ww1, ww3...) as one on BASE."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/")


def _kind(text):
    """From the site's type line: "Manga (Read Right to Left)" / "Type: Right(→) to Left(←)" is manga, and its
    "Manhwa (Read Left to Right)" / "Type: Left(←) to Right(→)" a webtoon."""
    text = (text or "").lower()
    if "manhwa" in text or "webtoon" in text or "manhua" in text:
        return "webtoon"
    right, left = text.find("right"), text.find("left")
    if right >= 0 and left >= 0:
        return "manga" if right < left else "webtoon"
    return "manga" if "manga" in text else ""


def _more(doc):
    return doc.one(".pagination a.next_p") is not None


def search(query, page=1):
    words = quote(query.strip().lower(), safe="")  # (what the site's own search box does: trimmed, lower case)
    doc = parse(_get(f"{BASE}/Find/{words}?page={page}").text)
    items, seen = [], set()
    for card in doc.select(".manga_search_item"):
        link = card.one("h3 a[href^='/Manga/']") or card.one("a[href^='/Manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        kind = card.one("strong")
        items.append({"url": url, "title": link.text, "cover": picture(card.one("img"), BASE),
                      "kind": _kind(kind.text if kind is not None else "")})
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    if list_id == "popular":
        doc = parse(_get(f"{BASE}/Genre/All/{page}").text)
        cards, title_css = doc.select(".ranking_item"), "h3"
    elif list_id == "latest":
        doc = parse(_get(f"{BASE}/Latest_Releases/{page}").text)
        cards, title_css = doc.select(".latest_releases_item"), "strong"
    else:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    items, seen = [], set()
    for card in cards:
        link = card.one("a[href^='/Manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        heading = link.one(title_css) or card.one(title_css)
        lines = " ".join(d.text for d in card.select("div"))
        items.append({"url": url, "title": heading.text if heading is not None else link.text,
                      "cover": picture(card.one("img"), BASE), "kind": _kind(lines)})
    return {"items": items, "more": bool(items) and _more(doc)}


def series(url):
    url = _on_site(url)
    if "/Read" in url:  # (a chapter's link: its page's heading links to the series)
        link = parse(_get(url).text).one("h1 a[href^='/Manga/'], a[href^='/Manga/']")
        if link is None:
            raise ValueError(f"That isn't a series' link on {NAME}: {url}")
        url = absolute(BASE, link.get("href"))
    m = re.match(r"(https://[^/]+/Manga/[^/?#]+)", url)
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1)
    doc = parse(_get(url).text)
    title = doc.one(".manga_series_data h1") or doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    lines = [d.text for d in doc.select(".manga_series_data div")]
    year = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("year published")), "")
    kind = _kind(next((l for l in lines if l.lower().startswith("type")), ""))
    if not kind:
        kind = _kind(" ".join(a.text for a in doc.select(".series_sub_genre_list a")))
    about = doc.one(".manga_series_description p")
    chapters, seen = [], set()
    for row in doc.select(".manga_series_list tr"):
        link = row.one("a.chapter-link") or row.one("a[href^='/Read']")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        cells = row.select("td")
        when = cells[1].text if len(cells) > 1 else ""
        name = link.text or "Chapter"
        num = number(name)
        if num is None:  # (the link's own number: /Read1_<Name>_<n>)
            m = re.search(r"_(\d+(?:\.\d+)?)[a-z]?$", href)
            num = float(m.group(1)) if m else None
        chapters.append({"url": href, "name": name, "number": num,
                         "date": when.replace("/", "-") if re.fullmatch(r"\d{4}/\d{2}/\d{2}", when) else when})
    if len(chapters) > 1 and chapters[0]["number"] is not None and chapters[-1]["number"] is not None \
            and chapters[0]["number"] > chapters[-1]["number"]:
        chapters.reverse()
    return {"url": url, "title": title.text, "cover": picture(doc.one(".manga_series_image img"), BASE),
            "summary": about.text if about is not None else "", "kind": kind,
            "year": int(year) if year.isdigit() else None, "chapters": chapters}


def pages(chapter_url):
    url = _on_site(chapter_url)
    if "/Read" not in url:
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    doc = parse(_get(url).text)
    found = []
    for img in doc.select(".mySlides img, img#gohere"):
        src = picture(img, BASE)
        if src and src not in found and "/share/" not in src:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
