#!/usr/bin/env python3
"""Publish a routine run in ONE plain command (shared-rules §0b and §15.2).

Why it exists: a scheduled run cannot answer a permission prompt, and a chained git command with an inline
heredoc commit message (git config ... || ...; git add ... && git commit -F - <<'EOF' ...) stopped the
2026-11-05 late Morning Brief at its commit. This does the whole §15.2 sequence itself.

Usage
  python3 .github/scripts/publish.py --subject "report: 2026-11-05 am" [--body-file FILE]
  python3 .github/scripts/publish.py --push-only          (after resolving a rebase conflict by hand)

Steps: set the git identity if unset; stage ONLY the routine write set (site/reports, state, ledgers,
registry); make ONE commit (subject + optional body from a file written with the Write tool); fetch and
rebase onto origin/main; push HEAD to main (retrying a non-fast-forward up to 3 times); if the push to main is
refused by the proxy or policy, push the session branch and open the §15.2b PR (titled with the subject).

Exit status: 0 published (main or PR), 2 nothing to commit, 3 rebase conflict (resolve per §15.2, then
--push-only), 1 anything else. Never force-pushes.
"""

import argparse
import os
import subprocess
import sys
import tempfile

WRITE_SET = ["site/reports", "state", "ledgers", "registry"]
BOT_NAME = "Intelligence Terminal Bot"
BOT_EMAIL = "loganshaffer87@gmail.com"


def git(*args, check=False):
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (proc.stderr or proc.stdout).strip()))
    return proc


def ensure_identity():
    if not git("config", "user.name").stdout.strip():
        git("config", "user.name", BOT_NAME, check=True)
    if not git("config", "user.email").stdout.strip():
        git("config", "user.email", BOT_EMAIL, check=True)


def stage_and_commit(subject, body_file):
    outside = []
    for line in git("status", "--porcelain", "-uall").stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if not any(path == d or path.startswith(d + "/") for d in WRITE_SET):
            outside.append(path)
    existing = [d for d in WRITE_SET if os.path.exists(d)]
    git("add", "-A", "--", *existing, check=True)
    if not git("diff", "--cached", "--name-only").stdout.strip():
        print("nothing to commit inside the write set")
        return False
    message = subject.strip() + "\n"
    if body_file:
        with open(body_file, encoding="utf-8") as handle:
            body = handle.read().strip()
        if body:
            message += "\n" + body + "\n"
    handle, path = tempfile.mkstemp(prefix="publish_msg_", suffix=".txt")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(message)
        git("commit", "-q", "-F", path, check=True)
    finally:
        os.unlink(path)
    stat = git("show", "--stat", "--format=%h %s", "HEAD").stdout.strip().splitlines()
    print("committed " + (stat[0] if stat else "?"))
    for line in stat[-1:]:
        print("  " + line.strip())
    if outside:
        print("NOT committed (outside the routine write set): " + ", ".join(sorted(set(outside))[:20]))
    return True


def rebase_on_main():
    git("fetch", "-q", "origin", "main", check=True)
    proc = git("rebase", "origin/main")
    if proc.returncode != 0:
        conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.split()
        print("REBASE CONFLICT in: " + ", ".join(conflicts))
        print("Resolve per shared-rules §15.2 (take the remote version of index.json / run-log.jsonl and re-apply your "
              "addition), git add the files, run: git rebase --continue, then: python3 .github/scripts/publish.py --push-only")
        return False
    return True


def push_main():
    for attempt in range(1, 4):
        proc = git("push", "origin", "HEAD:main")
        out = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode == 0:
            print("pushed to main: " + git("rev-parse", "--short", "HEAD").stdout.strip())
            return "main"
        if "non-fast-forward" in out or "fetch first" in out or "rejected" in out and "403" not in out:
            print("push %d rejected as non-fast-forward; rebasing again" % attempt)
            if not rebase_on_main():
                return "conflict"
            continue
        print("push to main refused: " + out.strip().splitlines()[-1] if out.strip() else "push to main refused")
        return "refused"
    return "refused"


def pr_fallback(subject):
    branch = git("branch", "--show-current").stdout.strip()
    if not branch or branch == "main":
        branch = "claude/report-" + subject.split(":", 1)[-1].strip().replace(" ", "-")
        git("checkout", "-q", "-b", branch, check=True)
    proc = git("push", "-u", "origin", "HEAD")
    if proc.returncode != 0:
        print("push of %s failed: %s" % (branch, (proc.stderr or proc.stdout).strip()))
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import open_pr  # noqa: E402 - the sibling helper
    return open_pr.main(["--title", subject.strip(), "--head", branch]) == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subject")
    parser.add_argument("--body-file")
    parser.add_argument("--push-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        ensure_identity()
        subject = args.subject or git("log", "-1", "--format=%s").stdout.strip()
        if not args.push_only:
            if not args.subject:
                parser.error("--subject is required unless --push-only")
            if not stage_and_commit(args.subject, args.body_file):
                return 2
        if not rebase_on_main():
            return 3
        result = push_main()
        if result == "main":
            return 0
        if result == "conflict":
            return 3
        print("falling back to the §15.2b pull request")
        return 0 if pr_fallback(subject) else 1
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
