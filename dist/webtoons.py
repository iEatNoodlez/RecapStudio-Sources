"""Webtoons: English Originals and Canvas series from www.webtoons.com, the official WEBTOON site.

Search (Originals, then Canvas), lists (the site's rankings, today's Originals and the completed ones), a series'
episodes and an episode's pictures at full size. Episodes come from the list the site's mobile pages load (all of
them at once); when that isn't there, from the series' own pages of ten. Episodes that are locked (Fast Pass,
Daily Pass: read in the app only) aren't on the website, so they aren't listed, and pages() says so for one that
got locked since. Links from the mobile site (m.webtoons.com) and episode links open too.
"""
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "Webtoons"
VERSION = "1.0.0"
SITES = ["webtoons.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "trending", "name": "Trending"},
         {"id": "latest", "name": "Latest (Originals updated today)"}, {"id": "canvas", "name": "Canvas: popular"},
         {"id": "completed", "name": "Completed Originals"}]
HEADERS = {"Referer": "https://www.webtoons.com/"}  # (its pictures are refused without it)
DELAY = 0
TEST_QUERY = "tower of god"

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

BASE = "https://www.webtoons.com"
MOBILE = "https://m.webtoons.com"
LANG = "en"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
LIST_PAGES = {"popular": "/ranking/popular", "trending": "/ranking/trending", "canvas": "/ranking/canvas",
              "latest": "/originals?sortOrder=UPDATE", "completed": "/originals/complete?sortOrder=MANA"}
PER_PAGE = 60          # (the lists are one long page on the site: given out this many at a time)
KEEP_LISTS = 600       # seconds a list is kept before it's fetched again
_lists = {}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url, missing=None, **kw):
    """A page, waiting once when the site says to slow down. missing: what to say when it isn't there (404)."""
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait)) if wait.isdigit() else 10)
            continue
        if r.status_code == 404:
            raise ValueError(missing or f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _link(url):
    """A picture's link with the spaces some of them have made safe."""
    return (url or "").strip().replace(" ", "%20")


def _canvas(url):
    return bool(re.search(r"/(canvas|challenge)/", urlparse(url).path))


def _cards(doc):
    """Series in a search result, ranking or list: [{"url", "title", "cover", "summary", "kind"}]."""
    out, seen = [], set()
    for a in doc.select("ul.webtoon_list a[href*='title_no='], .challenge_lst a[href*='title_no=']"):
        url = absolute(BASE, a.get("href"))
        no = parse_qs(urlparse(url).query).get("title_no", [""])[0]
        title = a.one(".title, .subj")
        title = title.text if title is not None else (a.one("img") or Node("img")).get("alt")
        if not no or no in seen or not title:
            continue
        seen.add(no)
        author, genre = a.one(".author"), a.one(".genre")
        summary = "Canvas series" if _canvas(url) else "WEBTOON Original"
        summary += f" by {author.text}" if author is not None and author.text else ""
        summary += f" · {genre.text}" if genre is not None and genre.text else ""
        out.append({"url": url, "title": title, "cover": _link(picture(a.one("img"), BASE)), "summary": summary,
                    "kind": "webtoon"})
    return out


def _has_page(doc, page):
    """Whether a page's numbered links go on to that page."""
    return any(re.search(rf"[?&]page={page}(&|$)", a.get("href"))
               for a in doc.select(".list_pagination a[href], .paginate a[href]"))


def search(query, page=1):
    """Originals first (the site's own series), then Canvas (its members' series), a page of each."""
    items, more = [], False
    for kind in ("originals", "canvas"):
        doc = parse(_get(f"{BASE}/{LANG}/search/{kind}", params={"keyword": query, "page": page}).text)
        items += _cards(doc)
        more = more or _has_page(doc, page + 1)
    items.sort(key=lambda x: x["title"].casefold() != query.strip().casefold())  # (the title asked for first)
    return {"items": items, "more": more}


def browse(list_id, page=1):
    if list_id not in LIST_PAGES:
        raise ValueError(f"{NAME} has no list {list_id}")
    kept = _lists.get(list_id)
    if not kept or time.time() - kept[0] > KEEP_LISTS:
        kept = _lists[list_id] = (time.time(), _cards(parse(_get(f"{BASE}/{LANG}{LIST_PAGES[list_id]}").text)))
    items = kept[1]
    start = (page - 1) * PER_PAGE
    return {"items": items[start:start + PER_PAGE], "more": start + PER_PAGE < len(items)}


def _title_no(url):
    query = parse_qs(urlparse(url).query)
    no = (query.get("title_no") or query.get("titleNo") or [""])[0]
    if not no.isdigit():
        raise ValueError(f"That isn't a {NAME} series link (one like {BASE}/en/fantasy/tower-of-god/list?title_no=95)")
    return no


def _list_url(url):
    """A series' page on the desktop site, from any link to it or one of its episodes (www. or m.)."""
    no = _title_no(url)
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 3 or "episodeList" in parts:  # (a short link: the site sends it on to the series' page)
        return f"{BASE}/{'challenge/' if _canvas(url) else ''}episodeList?titleNo={no}"
    return f"{BASE}/{'/'.join(parts[:3])}/list?title_no={no}"  # (the site puts right a wrong genre or name)


def _date(millis=None, text=""):
    if millis:  # (the day in the US, as the site shows it)
        return datetime.fromtimestamp(millis / 1000, timezone(timedelta(hours=-5))).date().isoformat()
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return text.strip()


def _from_api(no, lang, canvas):
    """Every free episode at once, as the mobile site gets them: [(title, link, date)], first episode first."""
    params = {"pageSize": 99999}
    if canvas:
        params["readingLanguageCode"] = lang
    r = _get(f"{MOBILE}/api/v1/{'canvas' if canvas else 'webtoon'}/{no}/episodes", params=params,
             headers={"Referer": MOBILE + "/"})
    found = r.json()
    if not found.get("success"):
        raise RuntimeError(f"{NAME}'s episode list said it failed")
    episodes = sorted(found["result"]["episodeList"], key=lambda e: e["episodeNo"])
    return [(e["episodeTitle"], absolute(BASE, e["viewerLink"]), _date(e.get("exposureDateMillis")))
            for e in episodes]


def _from_pages(list_url, first):
    """The same from the series' own pages, ten episodes a page, newest first (when the list above isn't there)."""
    found, seen, page, doc, started = [], set(), 1, first, time.time()
    while True:
        new = 0
        for li in doc.select("li._episodeItem"):
            link, title, date = li.one("a[href]"), li.one(".subj > span"), li.one(".date")
            if link is None or link.get("href") in seen:
                continue
            seen.add(link.get("href"))
            new += 1
            found.append((title.text if title is not None else "", absolute(BASE, link.get("href")),  # (not its "UP")
                          _date(text=date.text if date is not None else "")))
        if not new or not _has_page(doc, page + 1) or time.time() - started > 70:
            break
        page += 1
        time.sleep(0.3)  # (gently: a long series is many pages)
        doc = parse(_get(f"{list_url}&page={page}").text)
    return list(reversed(found))


def _names(episodes):
    """Chapters, numbered as the site numbers them (#1, #2...). The app places a chapter by the first number in its
    name, so when the titles' own numbers aren't those ("[Season 2] Ep. 5"), each name starts with its #."""
    def first(title):
        m = re.search(r"\d+(?:\.\d+)?", title)
        return float(m.group()) if m else None
    own = all(first(title) == i for i, (title, _, _) in enumerate(episodes, 1))
    return [{"url": url, "name": title if own else f"#{i} - {title}".rstrip(" -"), "number": i, "date": date}
            for i, (title, url, date) in enumerate(episodes, 1)]


def series(url):
    r = _get(_list_url(url), missing=f"{NAME} has no such series (it may have been taken down)")
    page_url = re.sub(r"[?&]webtoon-platform-redirect=true", "", r.url)
    doc = parse(r.text)

    def meta(name):
        tag = doc.one(f"meta[property='{name}']")
        return tag.get("content") if tag is not None else ""

    title = meta("og:title") or (doc.one("h1.subj") or Node("h1")).text
    if not title or not doc.select("#_listUl, li._episodeItem"):
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    summary = doc.one("p.summary")
    lang = ([p for p in urlparse(page_url).path.split("/") if p] or [LANG])[0]
    canvas = _canvas(page_url)
    try:
        episodes = _from_api(_title_no(page_url), lang, canvas)
    except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as e:
        print(f"the episode list didn't come ({e}): reading the series' pages instead")
        episodes = []
    if not episodes and doc.one("li._episodeItem") is not None:
        episodes = _from_pages(page_url.split("&")[0], doc)
    return {"url": page_url, "title": title.strip(), "cover": _link(meta("og:image")),
            "summary": summary.text if summary is not None else meta("og:description"), "kind": "webtoon",
            "chapters": _names(episodes)}


def pages(chapter_url):
    """An episode's pictures, at full size (the site's own links ask for a smaller copy: ?type=q90)."""
    parts = urlparse(chapter_url)
    url = BASE + parts.path + (f"?{parts.query}" if parts.query else "")  # (the desktop page, for m. links too)
    r = _get(url, missing=f"{NAME} doesn't show this episode on its website: it's locked (Fast Pass or Daily Pass, "
                          "read in the WEBTOON app only) or was taken down")
    doc = parse(r.text)
    found = []
    for img in doc.select("#_imageList img._images"):
        src = img.get("data-url") or img.get("src")
        if src and "bg_transparency" not in src:
            src = re.sub(r"\?type=[^&]*$|(?<=[?&])type=[^&]*&?", "", absolute(chapter_url, src)).rstrip("?&")
            if src not in found:
                found.append(_link(src))
    if not found:
        if "__motiontoonViewerState__" in r.text or doc.one("#ozViewer") is not None:
            raise RuntimeError(f"This {NAME} episode is a motion comic (moving layers, not pictures), which this "
                               "plugin can't save")
        raise RuntimeError(f"{NAME} showed no pictures for this episode (it may be locked: Fast Pass and Daily Pass "
                           "episodes are read in the WEBTOON app only)")
    return found
