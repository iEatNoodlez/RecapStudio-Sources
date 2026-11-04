---
name: make-a-source
description: Add a comic, manga or webtoon site to RecapStudio-Sources as a Recap Studio source plugin, or fix one that stopped working. It covers telling which shared engine a site is built on, adding it to that engine's list or writing a plugin of its own, building the list and checking the site for real. Use it whenever someone asks to add, support, write, fix or update a source, scraper or site for Recap Studio, even if they only paste a site's link.
---

# Making a Recap Studio source

Recap Studio turns comic chapters into narrated recap videos. It ships with no scrapers. Members add **source plugins**, one Python file per site, from plugin lists like this repository's `index.json`. This skill adds a site to that list, or fixes one, the way the repository already does it.

Work in a clone of this repository (RecapStudio-Sources). The result is a change under `sites/`, `engines/` or `plugins/`, plus what `tools/build.py` writes from it. Commit it only if the person you're working for asks you to.

## Ground rules

These are fixed. Keep to them even when a site makes them inconvenient.

- **Scrapers live here only.** Never add one to Recap Studio itself or to RecapStudio-Plugins (the official plugin list, which holds no scrapers).
- **Don't get around a site's protections.** That rules out FlareSolverr, cloudscraper, headless or hidden browsers, CAPTCHA solvers, faked TLS fingerprints, logins, paywalls, coin-locked chapters and unscrambling scrambled pictures. A site behind Cloudflare's browser check still gets a normal plugin. Recap Studio sees the check itself and lets the member pass it in a window (its Site check plugin). After that, the plugin's `requests` calls carry the member's pass. So:
  - make every page request with `requests` (a `Session` is fine), never `urllib`. The app hooks `requests` to detect the check and add the pass; a `urllib` request gets neither.
  - don't swallow a Cloudflare answer and return empty results. Raise an error or let it through, so the member is told.
- **Only what Python's standard library and `requests` provide.** Nothing else is installed for plugins. For HTML, use `lib/dom.py` (below).
- **Mark adult sites** with `"nsfw": true` (or `NSFW = True`). Recap Studio installs them switched off.
- **Never open, save or describe a site's pictures.** Some of these sites are adult. `tests/check.py` reads only a picture's first bytes to see that it is one. When you look at pages, read the HTML for picture links, never the pictures.
- **Be gentle with the site.** Use one session, retry a 429 once after its `Retry-After`, and set `delay` / `DELAY` for sites that limit how fast you ask.

## What a plugin is

One Python file, run by Recap Studio's own Python 3.12, each plugin in a process of its own. The full reference is [Writing a plugin](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/sources.md#writing-a-plugin), with a complete example in [plugin-example.py](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugin-example.py). In short:

The **settings** are plain values at the top, which the app reads without running the file:

| Setting | |
|---|---|
| `RECAP_PLUGIN = 1` | Required: the plugin interface (version 1). |
| `NAME` | Required: the name the app shows. |
| `VERSION` | Compared with the list's to offer updates. Raise it whenever the file changes. |
| `SITES` | Hosts whose links it opens, like `["example.com"]` (subdomains count). |
| `LANGUAGE`, `NSFW` | Shown next to its name. |
| `LISTS` | What `browse()` offers: `[{"id": "popular", "name": "Popular"}]`. |
| `HEADERS` | Sent with every picture the app downloads for it, usually `{"Referer": "https://site/"}`. |
| `DELAY` | Seconds between pictures, for sites that limit you. |
| `TEST_QUERY` | (This repository's own) what `tests/check.py` searches for. |

The **functions** take and return plain data. Raise an exception with a readable message when something's wrong: the member sees it.

| Function | Gets | Returns |
|---|---|---|
| `series(url)` (required) | A series' link. | `{"title", "cover", "summary", "kind", "chapters": [{"url", "name", "number", "date"}]}`. Only `title` and each chapter's `url` are required. **First chapter first.** `number` is a float or None. |
| `pages(chapter_url)` (required) | A chapter's `url`. | Its pictures' links in reading order, or `{"url", "headers"}` for one that needs headers of its own. |
| `search(query, page)` | The title typed, page from 1. | `{"items": [{"url", "title", "cover", "summary", "kind", "year"}], "more": bool}`. |
| `browse(list_id, page)` | One of `LISTS`' ids, and the page. | Like `search()`. |

`kind` is `"manga"`, `"webtoon"` or `"comic"` when you know it. Recap Studio uses it to pick the new project's kind. A call that takes over 90 seconds is stopped. Return chapters you can't get (paid, locked) as nothing: leave them out.

## How the repository is laid out

| | |
|---|---|
| `engines/<name>.py` | Code for every site built on one template or platform. Its first lines are `# engine:`, `# version:`, `# title:` and `# lists:` (JSON), then a comment saying what it does and its OPTIONS. |
| `sites/<engine>.json` | That engine's sites: `{"engine": "<name>", "sites": [...]}`. |
| `plugins/<id>.py` | A site with a layout of its own, as one complete plugin. |
| `lib/dom.py` | The HTML parser. A line `# @include dom` is replaced with it at build time. |
| `tools/build.py` | Writes `dist/<id>.py` for every site, `index.json` with their SHA-256s, and the README's table of sites. |
| `tests/check.py` | Runs each built file the way Recap Studio does, against the real site. |

`dist/`, `index.json` and the README's table between `<!-- sites -->` and `<!-- /sites -->` are written by `build.py`. Never edit them by hand.

A site entry in `sites/<engine>.json` has:
- `id`: the file name, lowercase, with no spaces;
- `name`;
- `base`: the site's address, with no trailing slash;
- `test`: a word to search for that finds something;
- when needed: `nsfw`, `language` (default `"en"`), `options` (what the engine's comment lists), `also` (old domains whose links it should open), `headers` (more picture headers), `delay`, `revision` and `summary`.

`build.py` writes the generated file's settings from the entry and defines `BASE`, `NAME` and `OPTIONS` before the engine's code, which uses them.

### lib/dom.py

`parse(html)` gives the document. Then:
- `node.select("css")` gives every match, and `node.one("css")` the first or None;
- `node.text` is all the text inside, `node.own_text` the node's own text without its children's;
- `node.get("attr")` reads an attribute;
- `absolute(base, href)` makes a full link;
- `picture(img, base)` gives an `<img>`'s real picture, trying lazy-loading attributes such as `data-src` before `src`;
- `number("Chapter 12.5")` gives `12.5`.

Selectors cover tag, `.class`, `#id`, `[attr]`, `[attr=v]`, `^=`, `$=`, `*=`, `~=`, descendant and `>` combinators, and `a, b` groups. There are no pseudo-classes, so filter in Python instead.

## Steps

### 1. Look at the site

With Recap Studio's Python (see step 4), fetch with `requests` the site's home page, its search for a common word, one series' page and one chapter's page. Use the same browser `User-Agent` the engines use. Save the HTML in a scratch folder **outside** the repository and read it. If the answer is Cloudflare's "Just a moment…" page (status 403 or 503, `cf-mitigated: challenge`), you can't look past it. Say so and stop, unless the site is clearly on an engine you can recognise from somewhere else.

### 2. Find what it's built on

Match it against the engines here. Read each engine's top comment and code for what it expects. These signs usually tell:

| Engine | Signs in the HTML |
|---|---|
| Madara | `wp-content/themes/madara`, `wp-manga` classes, chapters in `li.wp-manga-chapter`, search at `/?s=…&post_type=wp-manga`. |
| MangaThemesia | `ts_reader.run(` on chapter pages, chapters in `#chapterlist li` (with `.chapternum`), `.listupd .bsx` cards. |
| HeanCMS | A JSON API at `api.<site>` (`/query`, `/series/<slug>`, `/chapter/query`). |
| VTheme | A JSON API at `api.<site>/api` (`/query`, `/post`, `/chapters`). |
| Manganato, MangaHere | Those platforms' own sites and their sister domains. |

Then choose:
- **It's an engine's site:** add an entry to `sites/<engine>.json`, setting `options` when it moved its series' folder (`path`), needs cookies (an adult site's "show adult content" cookie), or is all manhwa (`"kind": "webtoon"`).
- **Nearly an engine's site, with one difference:** if other sites could share that difference, add an OPTION to the engine, document it in its top comment, and raise its `# version:`. Every site on the engine is then offered the update, so check them all (step 4). Otherwise, write a plugin.
- **Its own layout:** write `plugins/<id>.py`. Start from the closest existing plugin, keeping its order: docstring, settings, imports, `# @include dom`, session, `_get`, the functions.
- **A platform several sites share that isn't here yet:** write `engines/<name>.py` with its header lines and `sites/<name>.json`.

### 3. Write it

- **Docstring:** the first line is `"<Name>: chapters from <host>, …"`. Then say where each thing comes from (its page, its API, its script) and what the site needs (Referer and the like). The list shows that line as the plugin's summary.
- **Requests:** use a module-level `requests.Session()` with a browser `User-Agent` and the site as `Referer`. Give one `_get()` the timeout (45 s), the single retry for a 429 and readable errors. A 404 raises `ValueError(f"{NAME} has no such page: {url}")`.
- **Chapters:**
  - Many sites list newest first: reverse them.
  - Take the number with `number(name)`.
  - Leave out paid or locked chapters, which come later as new chapters once they're free.
  - Some sites load chapters with a separate request after the page. Find that request in the page's scripts and call it.
- **Pictures:**
  - Lazy-loaded `<img>`s keep the real link in `data-src` and the like (`picture()` handles it).
  - Some readers list the pictures in a script (`ts_reader.run({...})`, a JSON blob, a packed `eval(function(p,a,c,k,e,d)…)`). Parse what's there, but don't unscramble pictures.
  - Keep reading order and drop duplicates.
  - Raise a clear error when a chapter shows none.
- **Search and lists:** return what the site shows, with `"more"` when there's a next page.
- **Settings:**
  - A new plugin starts at `VERSION = "1.0.0"`, with `TEST_QUERY`.
  - Change a plugin: raise its `VERSION`.
  - Change an engine: raise its `# version:`.
  - Change only a site's entry: add or raise its `revision` (`"1"`, `"2"`…), so members are offered the update.

### 4. Build and check

Run both scripts with **Recap Studio's own Python**: `<Recap Studio>\.venv\Scripts\python.exe` in a source checkout, or `<Recap Studio>\python\python.exe` in the packaged app. `check.py` finds Recap Studio beside this folder (`..\App`, `..\RecapStudio`, `..\Release`), or takes `--app <its folder>`. If you can't find a Recap Studio, ask for its folder.

```powershell
python tools\build.py
python tests\check.py <id>
```

`build.py` refuses a file that doesn't compile, and an id that's in two places. `check.py` must say `ok` for each of these:
- the search finds something;
- the first result's series has chapters, mostly numbered, first chapter first;
- that chapter has pictures, and the first one's bytes are a picture;
- each list has series.

A `FAIL` line says what went wrong, and the site's own log is in the folder it names at the end. Fix it and run both again. After an engine change, run `check.py` on every site on that engine.

Some failures aren't yours to fix:
- The site is down.
- Every request gets Cloudflare's check. The member passes it in the app; check.py can't.
- The test word finds nothing. Pick another.

Report these as they are rather than changing the code to hide them.

### 5. Finish

The change is your files in `sites/`, `engines/` or `plugins/`, plus `dist/`, `index.json` and `README.md` as `build.py` wrote them. Line endings stay LF (`.gitattributes` sees to it); the checksums in `index.json` depend on it. If you're asked to commit, put the site and what it changed in the message, like `Add Example Scans (Madara)` or `Toonily: chapters load from the theme's request again`. Push only when asked. Members get the change the next time Recap Studio starts, or with the list's **Check now**.

Tell the person what you added or changed, what `check.py` said for each site you touched, and anything that doesn't work, such as Cloudflare, locked chapters or missing lists.
