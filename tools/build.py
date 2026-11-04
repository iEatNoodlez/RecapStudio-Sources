"""Build the list: one file in dist/ for each source, with everything it needs written into it (Recap Studio installs
a source plugin as one file), and index.json, the list Recap Studio reads, with each file's checksum.

- An engine's sites (sites/<engine>.json: {"engine", "sites": [...]}): dist/<id>.py is the site's settings (NAME, SITES, BASE, OPTIONS...) and then
  engines/<engine>.py. Its version is the engine's, with "-<revision>" when the site's own entry has one: a change
  to an engine raises its version, so every one of its sites is offered as an update.
- A site with its own layout: plugins/<id>.py, as it is.
Each site's entry, and each plugin's TEST_QUERY, says what tests/check.py searches it for.
Either way, a line "# @include dom" is replaced with lib/dom.py (the HTML parser). Files are written with LF line
endings, so their checksums are those of the files GitHub serves (.gitattributes keeps them so).

    python tools/build.py
"""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SETTINGS = ("RECAP_PLUGIN", "NAME", "VERSION", "SITES", "LANGUAGE", "NSFW")
LIST_NAME = "Recap Studio sources (RecapStudio-Sources)"


def include(text: str) -> str:
    dom = (ROOT / "lib" / "dom.py").read_text(encoding="utf-8")
    return re.sub(r"^# @include dom$", lambda m: dom.rstrip("\n"), text, flags=re.M)


def engine_meta(text: str) -> dict:
    """An engine's "# key: value" lines at its top."""
    meta = {}
    for line in text.splitlines():
        m = re.match(r"^# (\w+): (.+)$", line)
        if not m:
            break
        meta[m.group(1)] = m.group(2).strip()
    return meta


def site_file(site: dict) -> str:
    text = (ROOT / "engines" / f"{site['engine']}.py").read_text(encoding="utf-8")
    meta = engine_meta(text)
    base = site["base"].rstrip("/")
    host = urlparse(base).hostname.removeprefix("www.")
    version = meta["version"] + (f"-{site['revision']}" if site.get("revision") else "")
    settings = {
        "RECAP_PLUGIN": 1, "NAME": site["name"], "VERSION": version, "SITES": [host] + site.get("also", []),
        "LANGUAGE": site.get("language", "en"), "NSFW": bool(site.get("nsfw")),
        "HEADERS": {"Referer": base + "/", **site.get("headers", {})}, "LISTS": json.loads(meta.get("lists", "[]")),
        "DELAY": site.get("delay", 0), "BASE": base, "OPTIONS": site.get("options", {}),
    }
    head = (f'"""{site["name"]}: chapters from {host}, a site built on {meta["title"]}.\n\n'
            f"A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/"
            f'{site["engine"]}.py: change that, not this file."""\n')
    return head + "".join(f"{k} = {v!r}\n" for k, v in settings.items()) + "\n" + include(text)


def readme(entries: list, engines: dict) -> None:
    """README.md's table of sites, from what was built."""
    rows = ["| Site | Built on | Lists | Adult |", "|---|---|---|---|"]
    for e in sorted(entries, key=lambda e: e["name"].lower()):
        rows.append(f"| [{e['name']}](https://{e['sites'][0]}) | {engines.get(e['id']) or 'its own'} | "
                    f"{', '.join(e['lists']) or '–'} | {'18+' if e['nsfw'] else ''} |")
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    start = text.index("\n", text.index("<!-- sites")) + 1
    end = text.index("<!-- /sites -->")
    path.write_text(text[:start] + "\n".join(rows) + "\n" + text[end:], encoding="utf-8", newline="\n")


def settings_of(text: str) -> dict:
    found = {}
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") in SETTINGS + ("LISTS",)):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def main():
    built = {}
    for f in sorted((ROOT / "sites").glob("*.json")):
        group = json.loads(f.read_text(encoding="utf-8"))
        for s in group["sites"]:
            s = {"engine": group.get("engine"), **s}
            if s["id"] in built:
                sys.exit(f"{s['id']} is in two places ({f.name})")
            built[s["id"]] = (site_file(s), s)
    for f in sorted((ROOT / "plugins").glob("*.py")):
        if f.stem in built:
            sys.exit(f"{f.stem} is both in sites/ and plugins/")
        built[f.stem] = (include(f.read_text(encoding="utf-8")), {})
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob("*.py"):
        if old.stem not in built:
            old.unlink()
    entries = []
    for pid, (text, site) in sorted(built.items()):
        compile(text, f"dist/{pid}.py", "exec")  # (a mistake shows here, not in the app)
        data = text.encode("utf-8")
        (DIST / f"{pid}.py").write_bytes(data)
        s = settings_of(text)
        host = s["SITES"][0] if s.get("SITES") else ""
        entries.append({"id": pid, "name": s["NAME"], "version": s.get("VERSION", "1"), "file": f"dist/{pid}.py",
                        "sha256": hashlib.sha256(data).hexdigest(), "sites": s.get("SITES", []),
                        "language": s.get("LANGUAGE", ""), "nsfw": bool(s.get("NSFW")),
                        "lists": [x["name"] for x in s.get("LISTS", [])],
                        "summary": site.get("summary") or (ast.get_docstring(ast.parse(text)) or "").split("\n")[0]
                        or f"Chapters from {host}"})
        print(f"{pid:22} {s.get('VERSION', '1'):10} {'18+ ' if s.get('NSFW') else '    '}{len(data) // 1024:4} KB  dist/{pid}.py")
    index = {"name": LIST_NAME, "plugins": entries}
    (ROOT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"index.json: {len(entries)} sources")
    engines = {}
    for pid, (_, site) in built.items():
        if site.get("engine"):
            meta = engine_meta((ROOT / "engines" / f"{site['engine']}.py").read_text(encoding="utf-8"))
            engines[pid] = meta.get("engine", site["engine"])
    readme(entries, engines)


if __name__ == "__main__":
    main()
