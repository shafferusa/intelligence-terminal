#!/usr/bin/env python3
"""HTTP fetch helper for unattended routine runs (shared-rules §0b).

Why it exists: a scheduled run cannot answer a permission prompt, and Claude Code's shell-safety check prompts on
commands that put a brace expansion inside quotes, e.g. curl "...&api_key=${FRED_API_KEY}" or -w '%{http_code}'.
The 2026-11-05 Morning Brief stalled for hours on exactly that. This helper keeps every command line plain: keys are
named as @NAME@ placeholders and read from the environment here, and secret values are never printed.

Usage
  python3 .github/scripts/api_get.py URL [-o FILE] [-H "Name: value"]... [-A UA] [--timeout S]
  python3 .github/scripts/api_get.py --batch LIST [--dir DIR]
  python3 .github/scripts/api_get.py --wait-200 URL [--tries N] [--sleep S]

  @NAME@ in a URL or header is replaced by the environment variable NAME. @A|B@ takes the first one that is set.
  Date tokens use the America/New_York clock: @ET_DATE@ (2026-11-05), @ET_YYYYMMDD@ (20261105),
  @ET_YYYYMM@ (202611), @ET_YEAR@ (2026).
  Without -o the body is printed to stdout after a one-line status on stderr.
  LIST (for --batch, written with the Write tool): one fetch per line, fields separated by TABs:
      outfile<TAB>URL[<TAB>Header: value]...
  (with no headers, spaces work too). Blank lines and lines starting with # are skipped.
  .gov hosts get the plain project User-Agent (LoganTerminal/1.0, no contact details) automatically.
  SEC hosts (sec.gov and its sub-hosts) require a contact address from automated clients. They are refused,
  and nothing is sent, unless the environment variable PROJECT_CONTACT_EMAIL holds a dedicated project
  address (never a personal one). A refused SEC fetch prints a SEC-PAUSED line and counts as a failed fetch.

Exit status: 0 when every fetch returned 2xx (or --wait-200 reached 200), 1 otherwise, 2 on usage errors,
3 when a placeholder names an unset variable.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

GOV_UA = "LoganTerminal/1.0"
SEC_CONTACT_ENV = "PROJECT_CONTACT_EMAIL"  # a dedicated project address; never a personal one
DEFAULT_UA = "curl/8.5.0"
PLACEHOLDER = re.compile(r"@([A-Z][A-Z0-9_]*(?:\|[A-Z][A-Z0-9_]*)*)@")


class MissingVar(Exception):
    pass


class ContactRequired(Exception):
    """An SEC host was asked for while no project contact address is configured."""


def secret_values():
    out = []
    for name, value in os.environ.items():
        if value and len(value) >= 6 and re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", name):
            out.append(value)
    return sorted(out, key=len, reverse=True)


def redact(text):
    for value in secret_values():
        text = text.replace(value, "***")
    return text


def et_now():
    """Now on the America/New_York clock; falls back to fixed UTC-5 if the tz database is missing."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - never let a date token break a fetch
        return datetime.now(timezone(timedelta(hours=-5)))


def et_tokens():
    now = et_now()
    return {"ET_DATE": now.strftime("%Y-%m-%d"), "ET_YYYYMMDD": now.strftime("%Y%m%d"),
            "ET_YYYYMM": now.strftime("%Y%m"), "ET_YEAR": now.strftime("%Y")}


def expand(text):
    dates = et_tokens() if "@ET_" in text else {}

    def sub(match):
        if match.group(1) in dates:
            return dates[match.group(1)]
        for name in match.group(1).split("|"):
            value = os.environ.get(name, "")
            if value:
                return value
        raise MissingVar(match.group(1))
    return PLACEHOLDER.sub(sub, text)


SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def url_host(url):
    """The host curl would contact: a URL without a scheme defaults to http://, %-escapes are decoded,
    case and trailing dots are ignored."""
    text = url.strip()
    if not SCHEME.match(text):
        text = "http://" + text.lstrip("/")
    host = urllib.parse.urlsplit(text).hostname or ""
    return urllib.parse.unquote(host).strip().lower().rstrip(".")


def is_sec_host(host):
    return host == "sec.gov" or host.endswith(".sec.gov")


def user_agent(url, override):
    host = url_host(url)
    if is_sec_host(host):
        # The SEC asks automated clients for a contact address. Owner decision 2026-11-11: never a personal
        # one, so SEC requests stay paused until a project address is configured. Fail closed: the caller
        # sends nothing. An -A override cannot lift this.
        contact = os.environ.get(SEC_CONTACT_ENV, "").strip()
        if "@" not in contact or any(ch.isspace() for ch in contact):
            raise ContactRequired(host)
        return "%s (%s)" % (GOV_UA, contact)
    if override:
        return override
    return GOV_UA if host.endswith(".gov") else DEFAULT_UA


def fetch(url, out_path, headers, ua, timeout):
    """Fetch url to out_path. Returns (status, size). status 0 means a network error."""
    curl = shutil.which("curl")
    if curl:
        argv = [curl, "-sS", "-L", "--max-time", str(timeout), "-A", ua, "-o", out_path,
                "-w", "%{http_code} %{size_download}"]
        for header in headers:
            argv += ["-H", header]
        argv.append(url)
        proc = subprocess.run(argv, capture_output=True, text=True)
        parts = (proc.stdout or "").split()
        status = int(parts[0]) if parts and parts[0].isdigit() else 0
        size = int(float(parts[1])) if len(parts) > 1 else 0
        if proc.returncode != 0 and proc.stderr:
            sys.stderr.write(redact("curl: " + proc.stderr.strip()) + "\n")
        return status, size
    request = urllib.request.Request(url, headers={"User-Agent": ua})
    for header in headers:
        name, _, value = header.partition(":")
        request.add_header(name.strip(), value.strip())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body, status = exc.read(), exc.code
    except Exception as exc:  # noqa: BLE001 - report and carry on
        sys.stderr.write(redact("fetch error: %s" % exc) + "\n")
        return 0, 0
    with open(out_path, "wb") as handle:
        handle.write(body)
    return status, len(body)


def one(url_t, out, header_t, ua_override, timeout, label=None):
    try:
        url = expand(url_t)
        headers = [expand(h) for h in header_t]
    except MissingVar as exc:
        sys.stderr.write("missing env %s for %s\n" % (exc, label or url_t))
        return 3
    target = out
    temp = None
    if not target:
        handle, temp = tempfile.mkstemp(prefix="api_get_")
        os.close(handle)
        target = temp
    else:
        parent = os.path.dirname(os.path.abspath(target))
        os.makedirs(parent, exist_ok=True)
    try:
        ua = user_agent(url, ua_override)
    except ContactRequired as exc:
        if temp:
            os.unlink(temp)
        sys.stderr.write("SEC-PAUSED 0B %s (no project contact in %s; nothing was sent to %s)\n"
                         % (out or label or redact(url), SEC_CONTACT_ENV, exc))
        return 1
    status, size = fetch(url, target, headers, ua, timeout)
    shown = label or redact(url)
    sys.stderr.write("%s %dB %s\n" % (status or "ERR", size, out or shown))
    if temp:
        try:
            with open(temp, "rb") as handle:
                sys.stdout.write(redact(handle.read().decode("utf-8", "replace")))
        finally:
            os.unlink(temp)
    return 0 if 200 <= status < 300 else 1


def batch(list_path, out_dir, ua_override, timeout):
    worst = 0
    with open(list_path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split("\t") if "\t" in line else line.split()
            if len(fields) < 2:
                sys.stderr.write("skipped (need outfile and URL): %s\n" % redact(line))
                worst = max(worst, 1)
                continue
            out = os.path.join(out_dir, fields[0]) if out_dir else fields[0]
            code = one(fields[1].strip(), out, [f.strip() for f in fields[2:] if f.strip()], ua_override,
                       timeout, label=fields[0])
            worst = max(worst, code)
    return worst


def wait_200(url_t, tries, sleep_s, timeout):
    url = expand(url_t)
    try:
        ua = user_agent(url, None)
    except ContactRequired as exc:
        sys.stderr.write("SEC-PAUSED (no project contact in %s; nothing was sent to %s)\n" % (SEC_CONTACT_ENV, exc))
        print("ERR")
        return 1
    for attempt in range(1, tries + 1):
        handle, temp = tempfile.mkstemp(prefix="api_get_")
        os.close(handle)
        try:
            status, _ = fetch(url, temp, [], ua, timeout)
        finally:
            os.unlink(temp)
        sys.stderr.write("attempt %d: %s\n" % (attempt, status or "ERR"))
        if status == 200:
            print("200")
            return 0
        if attempt < tries:
            time.sleep(sleep_s)
    print(str(status or "ERR"))
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?")
    parser.add_argument("-o", "--out")
    parser.add_argument("-H", "--header", action="append", default=[])
    parser.add_argument("-A", "--user-agent")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--batch")
    parser.add_argument("--dir")
    parser.add_argument("--wait-200", dest="wait", action="store_true")
    parser.add_argument("--tries", type=int, default=9)
    parser.add_argument("--sleep", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.batch:
            return batch(args.batch, args.dir, args.user_agent, args.timeout)
        if not args.url:
            parser.print_usage(sys.stderr)
            return 2
        if args.wait:
            return wait_200(args.url, args.tries, args.sleep, args.timeout)
        return one(args.url, args.out, args.header, args.user_agent, args.timeout)
    except MissingVar as exc:
        sys.stderr.write("missing env %s\n" % exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
