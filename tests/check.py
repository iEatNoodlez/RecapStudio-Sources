"""Try the list's sources on their real sites, the way Recap Studio runs them: each built file (dist/) read by the
app's own plugin reader, run in the app's own plugin process, and asked to search, open the first result, list its
chapters and give a chapter's pictures. The first picture's first bytes are fetched to see it is a picture (never kept
or shown: some of these sites are adult), and each list (Popular, Latest...) is asked for its first page.

    python tests/check.py                 every source (with the app beside this folder: ..\\App, ..\\Release)
    python tests/check.py toonily asura   only those
    python tests/check.py --app D:\\RecapStudio

Run it with Recap Studio's own Python (its .venv, or the packaged app's python folder). What each source is
searched for: its "test" in sites/*.json, or TEST_QUERY in plugins/<id>.py; "the" when it has none. Nothing touches your own
Recap Studio: its data folder here is a temporary one.
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"\x00\x00\x00")  # JPEG, PNG, GIF, WebP, AVIF/HEIC (ftyp)


def find_app(given):
    for p in [Path(given)] if given else [ROOT.parent / n for n in ("App", "RecapStudio", "Release")]:
        if (p / "recap" / "plugin_host.py").is_file():
            return p.resolve()
    sys.exit("Recap Studio not found: give its folder with --app")


class Host:
    """One plugin in the app's plugin process, as sources.py runs it."""

    def __init__(self, app, path, log):
        self.proc = subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(app / "recap" / "plugin_host.py"), str(path)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                     encoding="utf-8", bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(x) for x in self.proc.stdout], daemon=True).start()

    def call(self, name, *args, timeout=90):
        self.proc.stdin.write(json.dumps({"call": name, "args": list(args)}) + "\n")
        self.proc.stdin.flush()
        try:
            answer = json.loads(self.lines.get(timeout=timeout))
        except queue.Empty:
            raise RuntimeError(f"{name}() took over {timeout} s (the app would stop it)")
        if not answer["ok"]:
            raise RuntimeError(answer["error"])
        return answer["result"]

    def stop(self):
        self.proc.kill()


def first_bytes(url, headers):
    r = requests.get(url, headers=headers, timeout=45, stream=True)
    try:
        head = r.raw.read(16, decode_content=True)
    finally:
        r.close()
    return r.status_code, r.headers.get("Content-Type", ""), head


def check(app, sources, pid, query):
    path = ROOT / "dist" / f"{pid}.py"
    lines, ok = [], True

    def say(good, text):
        nonlocal ok
        ok = ok and good
        lines.append(("  ok   " if good else "  FAIL ") + text)

    try:
        info = sources.read_plugin(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"  FAIL the app won't take it: {e}"]
    headers = {"User-Agent": sources.USER_AGENT, **info["headers"]}
    log = open(Path(os.environ["RECAP_DATA"]) / f"{pid}.log", "w", encoding="utf-8")
    host = Host(app, path, log)
    try:
        start = time.time()
        if not info["search"]:
            say(True, "no search (links and lists only)")
            found = sources._items(host.call("browse", info["lists"][0]["id"], 1), info["headers"]) if info["lists"] else {"items": []}
        else:
            found = sources._items(host.call("search", query, 1), info["headers"])
            say(bool(found["items"]), f'search "{query}": {len(found["items"])} results{", more pages" if found["more"] else ""}'
                f'{", e.g. " + found["items"][0]["title"] if found["items"] else ""} ({time.time() - start:.1f} s)')
        if not found["items"]:
            return ok, lines
        item = found["items"][0]
        start = time.time()
        s = sources._series(host.call("series", item["ref"]), item["ref"], info["headers"])
        chapters = s["chapters"]
        numbered = sum(c["number"] is not None for c in chapters)
        say(bool(chapters), f'series "{s["title"]}": {len(chapters)} chapters ({numbered} numbered), '
            f'{"a cover" if s["cover"] else "no cover"}, {"a summary" if s["summary"] else "no summary"}'
            f'{", " + s["kind"] if s["kind"] else ""} ({time.time() - start:.1f} s)')
        if chapters:
            say(chapters[0]["number"] is None or chapters[0]["number"] <= chapters[-1]["number"],
                f'first chapter first: "{chapters[0]["name"]}" … "{chapters[-1]["name"]}"')
            start = time.time()
            got = host.call("pages", chapters[0]["ref"])
            say(bool(got), f'pages of "{chapters[0]["name"]}": {len(got)} pictures ({time.time() - start:.1f} s)')
            if got:
                first = got[0] if isinstance(got[0], str) else got[0]["url"]
                extra = {} if isinstance(got[0], str) else got[0].get("headers") or {}
                status, kind, head = first_bytes(first, {**headers, **extra})
                say(status == 200 and head.startswith(MAGIC), f"its first picture: HTTP {status}, {kind or 'no type'}"
                    + ("" if head.startswith(MAGIC) else f", starts {head[:8]!r}"))
        for lst in info["lists"]:
            try:
                n = len(sources._items(host.call("browse", lst["id"], 1), info["headers"])["items"])
                say(n > 0, f'{lst["name"]}: {n} series')
            except Exception as e:
                say(False, f'{lst["name"]}: {e}')
    except Exception as e:
        say(False, str(e))
    finally:
        host.stop()
        log.close()
    return ok, lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--app")
    args = ap.parse_args()
    app = find_app(args.app)
    os.environ["RECAP_DATA"] = tempfile.mkdtemp(prefix="recap-sources-check-")
    sys.path.insert(0, str(app))
    from recap import sources
    queries = {}
    for f in (ROOT / "sites").glob("*.json"):
        queries.update({x["id"]: x.get("test", "the") for x in json.loads(f.read_text(encoding="utf-8"))["sites"]})
    for f in (ROOT / "plugins").glob("*.py"):
        m = re.search(r"^TEST_QUERY = [\"'](.+)[\"']$", f.read_text(encoding="utf-8"), re.M)
        if m:
            queries[f.stem] = m.group(1)
    ids = args.ids or sorted(p.stem for p in (ROOT / "dist").glob("*.py"))
    failed = []
    for pid in ids:
        ok, lines = check(app, sources, pid, queries.get(pid, "the"))
        print(f"{'ok  ' if ok else 'FAIL'} {pid}", *lines, sep="\n", flush=True)
        if not ok:
            failed.append(pid)
    print(f"\n{len(ids) - len(failed)} of {len(ids)} work" + (f"; failing: {', '.join(failed)}" if failed else ""),
          f"(their logs: {os.environ['RECAP_DATA']})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
