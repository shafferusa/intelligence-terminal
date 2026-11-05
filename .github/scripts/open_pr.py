#!/usr/bin/env python3
"""Open the §15.2b fallback pull request without an inline JSON curl (shared-rules §0b).

Usage
  python3 .github/scripts/open_pr.py --title "report: 2026-11-05 am" [--body TEXT] [--base main] [--head BRANCH]

The head defaults to the current git branch. Authentication is $GITHUB_TOKEN, which in the routine sandbox is the
literal placeholder the GitHub proxy replaces with real credentials for this repository. Prints the PR number and URL.
Exit status: 0 created (or already open for this head), 1 failed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = "shafferusa/intelligence-terminal"
API = "https://api.github.com/repos/%s/pulls" % REPO


def current_branch():
    return subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="Automated report publish.")
    parser.add_argument("--base", default="main")
    parser.add_argument("--head")
    args = parser.parse_args(argv)
    head = args.head or current_branch()
    if not head:
        sys.stderr.write("no current branch; pass --head\n")
        return 1
    token = os.environ.get("GITHUB_TOKEN", "proxy-injected")
    payload = {"title": args.title, "head": head, "base": args.base, "body": args.body}
    handle, data_path = tempfile.mkstemp(prefix="open_pr_", suffix=".json")
    os.close(handle)
    handle, out_path = tempfile.mkstemp(prefix="open_pr_out_", suffix=".json")
    os.close(handle)
    try:
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        curl = shutil.which("curl") or "curl"
        argv_curl = [curl, "-sS", "-X", "POST", API, "-H", "Authorization: Bearer " + token,
                     "-H", "Accept: application/vnd.github+json", "-d", "@" + data_path,
                     "-o", out_path, "-w", "%{http_code}"]
        proc = subprocess.run(argv_curl, capture_output=True, text=True)
        status = proc.stdout.strip()
        try:
            with open(out_path, encoding="utf-8") as f:
                reply = json.load(f)
        except (OSError, ValueError):
            reply = {}
    finally:
        for path in (data_path, out_path):
            try:
                os.unlink(path)
            except OSError:
                pass
    if status == "201":
        print("PR #%s %s" % (reply.get("number"), reply.get("html_url")))
        return 0
    message = json.dumps(reply.get("errors") or reply.get("message") or proc.stderr.strip())
    if "already exists" in message:
        print("PR already open for %s" % head)
        return 0
    sys.stderr.write("PR create failed: HTTP %s %s\n" % (status or "ERR", message[:500]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
