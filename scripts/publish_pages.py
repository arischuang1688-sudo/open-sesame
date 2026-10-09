"""Rebuild the existing branch-based Pages site and verify committed public data.

A GITHUB_TOKEN data commit does not itself trigger a Pages build. Never change
site configuration, never publish a working-tree candidate rejected by freshness,
and never send the GitHub token to the public website.
"""
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from .scheduled_update import complete_snapshot
except ImportError:
    from scheduled_update import complete_snapshot


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def api(repo, path, method="GET", allow_missing=False):
    request = Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=b"{}" if method == "POST" else None,
        method=method,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "open-sesame-pages-publisher"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404 and allow_missing:
            return {}
        raise RuntimeError(f"Pages API {method} {path}: HTTP {exc.code}") from exc


def public_text(url):
    # Public CDN requests intentionally have NO Authorization header.
    request = Request(url, headers={"User-Agent": "open-sesame-pages-verifier", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def publish(repo, sha, expected, attempts=40, sleep=time.sleep):
    day = (expected.get("market", {}).get("quote", {}).get("date", ""))
    if not complete_snapshot(expected, day, require_margin=False):
        raise RuntimeError("PAGES FAILED: committed dashboard lacks same-day 200-stock validation")
    site = api(repo, "pages")
    source = site.get("source") or {}
    if site.get("build_type") != "legacy" or source.get("branch") != "main" or source.get("path") != "/":
        raise RuntimeError("PAGES FAILED: expected existing main/root branch publishing; configuration was NOT changed")
    url = site.get("html_url", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != repo.split("/")[0].lower() + ".github.io":
        raise RuntimeError("PAGES FAILED: unexpected public site URL")
    if api(repo, "git/ref/heads/main")["object"]["sha"] != sha:
        raise RuntimeError("PAGES FAILED: main advanced; retry against the latest tested source")
    latest = api(repo, "pages/builds/latest", allow_missing=True)
    if not (latest.get("commit") == sha and latest.get("status") in ("built", "building", "queued")):
        api(repo, "pages/builds", method="POST")
        print(f"[pages] requested build commit={sha}")
    else:
        print(f"[pages] reuse existing {latest.get('status')} build commit={sha}")
    for attempt in range(attempts):
        build = api(repo, "pages/builds/latest")
        if build.get("commit") == sha:
            if build.get("status") == "errored":
                raise RuntimeError("PAGES FAILED: GitHub Pages build errored")
            if build.get("status") == "built":
                try:
                    actual = json.loads(public_text(url.rstrip("/") + f"/data/dashboard.json?sha={sha}&ts={time.time_ns()}"))
                    if actual == expected:
                        message = f"[pages] VERIFIED commit={sha} trading_date={day} stocks=200 url={url}"
                        print(message)
                        if os.environ.get("GITHUB_STEP_SUMMARY"):
                            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                                f.write(message + "\n")
                        return
                except (OSError, ValueError):
                    pass  # CDN may still serve the previous deployment.
        if attempt + 1 < attempts:
            sleep(10)
    raise RuntimeError("PAGES FAILED: build/CDN did not match the committed dashboard before timeout")


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = git("rev-parse", "HEAD")
    # Read HEAD, not data/dashboard.json: freshness can reject an uncommitted file.
    expected = json.loads(git("show", "HEAD:data/dashboard.json"))
    publish(repo, sha, expected)


if __name__ == "__main__":
    main()
