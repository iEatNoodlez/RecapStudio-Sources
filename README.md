# Recap Studio sources

Source plugins that get chapters from sites, for [Recap Studio](https://github.com/iEatNoodlez/RecapStudio). They're kept here, apart from [Recap Studio's own plugin list](https://github.com/iEatNoodlez/RecapStudio-Plugins), on purpose: that list never holds scrapers, and Recap Studio comes with none.

**You choose what to use.** Nothing here is installed until you pick it, and these sites' content belongs to its owners. Using a source in line with a site's terms and your local law is your responsibility.

## Adding them to Recap Studio

1. **Settings → Sources → Plugin lists**: paste this list's link and press **Add list**:
   ```
   https://raw.githubusercontent.com/iEatNoodlez/RecapStudio-Sources/main/index.json
   ```
2. Every site on it is installed, each checked against the list's SHA-256 first, and works at once, with no restart. Adult ones are installed switched off. Switch off the ones you don't use (from Recap Studio 0.2.5; before that, **Plugins…** → **Install** each one).
3. In a project, **Chapters → Get chapters from a site**: paste a series' link, or search one site (or **All your sources**, from Recap Studio 0.2.3). **Check for new chapters** and **New chapters on a schedule** use them too.

When a site changes its pages, its source stops working until it's fixed here. Recap Studio gets the new version the next time it starts (or with the list's **Check now**), and sites added here are installed then too.

## The sites

<!-- sites: written by tools/build.py -->
| Site | Built on | Lists | Adult |
|---|---|---|---|
| [Asura Scans](https://asurascans.com) | its own | Popular, Latest |  |
| [Drake Scans](https://drakecomic.net) | its own | Popular, Latest |  |
| [FanFox (MangaFox)](https://fanfox.net) | MangaHere | Popular, Latest |  |
| [Flame Comics](https://flamecomics.xyz) | its own | Popular, Latest |  |
| [Hive Toons](https://hivetoons.org) | VTheme | Popular, Latest |  |
| [King of Shojo](https://kingofshojo.com) | MangaThemesia | Popular, Latest |  |
| [Madara Scans](https://madarascans.net) | MangaThemesia | Popular, Latest |  |
| [Manga18fx](https://manga18fx.com) | its own | Popular, Latest | 18+ |
| [MangaDex](https://mangadex.org) | its own | Popular, Latest |  |
| [MangaHere](https://mangahere.cc) | MangaHere | Popular, Latest |  |
| [MangaKakalot](https://kakamanga.com) | Manganato | Popular, Latest, New, Completed |  |
| [MangaKatana](https://mangakatana.com) | its own | Hot, Latest updates, New manga |  |
| [MangaRead](https://mangaread.org) | Madara | Popular, Latest |  |
| [Manhwatop](https://manhwatop.com) | Madara | Popular, Latest |  |
| [Mgeko](https://mgeko.cc) | its own | Popular, Popular this week, Latest updates, Recently added |  |
| [Omega Scans](https://omegascans.org) | HeanCMS | Popular, Latest | 18+ |
| [Rizz Fables](https://rizzfables.com) | its own | Popular, Latest |  |
| [Toonily](https://toonily.com) | Madara | Popular, Latest | 18+ |
| [Vortex Scans](https://vortexscans.org) | VTheme | Popular, Latest |  |
| [Webtoons](https://webtoons.com) | its own | Popular, Trending, Latest (Originals updated today), Canvas: popular, Completed Originals |  |
| [WeebCentral](https://weebcentral.com) | its own | Popular, Latest updates, Recently added |  |
| [ZinManga](https://zinmanga.net) | its own | Popular, Latest, Trending |  |
<!-- /sites -->

**Built on** says which shared engine a site uses. Many sites are built on the same few templates (Madara, MangaThemesia, HeanCMS), so one engine serves them all. "Its own" is a site with a plugin of its own.

**What doesn't work:** sites behind Cloudflare's browser check, which only a real browser passes; sites whose pictures are scrambled; and paid or locked chapters. A site that turns on Cloudflare's check later says so when it's asked.

## How it's made

| | |
|---|---|
| `engines/<name>.py` | An engine: the code for every site built on one template. Its first lines say its name, version and lists. |
| `sites/<engine>.json` | That engine's sites: `id`, `name`, `base` (the site's address), `nsfw`, `language`, `options` (what differs between them) and `test` (a title to search for when checking). |
| `plugins/<id>.py` | A site with a layout of its own, as one file (with `TEST_QUERY` for checking). |
| `lib/dom.py` | A small HTML parser with CSS selectors. Plugins have only Python's standard library and `requests`, so `# @include dom` writes it into each file that needs it. |
| `tools/build.py` | Writes `dist/<id>.py` for every site (one file each, with everything it needs, as Recap Studio installs them), `index.json` with their checksums, and the table above. |
| `tests/check.py` | Tries every site for real, the way Recap Studio runs it: search, a series' chapters, a chapter's pictures, the lists. |

**Adding a site built on an engine that's here:** add its entry to that engine's `sites/<engine>.json`, then:

```powershell
python tools\build.py
python tests\check.py <its id>
```

Use Recap Studio's own Python (`<Recap Studio>\.venv\Scripts\python.exe` from source, or the packaged app's `python\python.exe`). check.py finds Recap Studio beside this folder, or takes `--app <its folder>`. It reads only the first bytes of a picture, never keeps or shows one.

**Changing an engine:** raise the `version` in its first lines, so every site on it is offered as an update.

**With an AI agent:** [skills/make-a-source/SKILL.md](skills/make-a-source/SKILL.md) is a skill for Claude Code and other agents. It walks them through adding a site or fixing one, the way this repository does it: telling which engine a site is built on, writing its entry or plugin, building and checking. It also covers what never goes in, such as getting around Cloudflare or locked chapters. Point your agent at it, or copy the folder into your agent's skills (`.claude/skills/` for Claude Code).

How plugins are written (what each function gets and gives) is in Recap Studio's docs: [Getting chapters from sites](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/sources.md#writing-a-plugin). Many sites' layouts were worked out from [HaruNeko](https://github.com/manga-download/haruneko)'s connectors (public domain).

## License

MIT. The sites' content isn't covered: it belongs to their owners.
