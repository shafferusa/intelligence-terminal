"""FinSim1's own Chrome DevTools Protocol client on the standard library only (FINSIM1_PLAN P8 task 1).

The test interpreters (py-3.12 and py-3.14) have no websockets package and Playwright is not installed for py-3.12, so the browser
smoke test (``test_terminal_browser_smoke``) drives a headless Edge / Chrome through this module: start the browser on a throwaway
profile, open pages, evaluate JavaScript, record every console error, uncaught exception and HTTP status the page saw, take a
screenshot when asked.  The approach is copied from ``tests/_cdp.py``, which stays untouched because it is FinSim2's.

Callers skip when ``find_browser()`` is None (no Chromium-family browser on the machine), with that reason.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
from typing import Dict, List, Optional

CANDIDATES = [os.path.join(r, x) for r in (os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                                            os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", ""))
              if r for x in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")]
NO_BROWSER = "no Edge or Chrome on this machine (looked in Program Files, Program Files (x86) and LocalAppData)"


def find_browser() -> Optional[str]:
    """The first Edge / Chrome executable found, else None (the caller skips with ``NO_BROWSER``)."""
    env = os.environ.get("FINSIM_CDP_BROWSER")
    if env and os.path.exists(env):
        return env
    return next((p for p in CANDIDATES if os.path.exists(p)), None)


class _WS:
    """A client WebSocket (RFC 6455): text frames out (masked), frames in (ping / pong skipped, close raises)."""

    def __init__(self, url: str, timeout: float = 30.0):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake refused: {head[:80]!r}")

    def send(self, text: str) -> None:
        data = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self) -> str:
        msg = b""
        while True:
            b0, b1 = self._exact(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else None
            payload = self._exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 0x8:
                raise ConnectionError("websocket closed by the browser")
            if op in (0x9, 0xA):
                continue
            msg += payload
            if b0 & 0x80:
                return msg.decode("utf-8")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """A headless browser and one CDP connection.

    ``page(url)`` opens a tab and returns its session id (Runtime, Log, Network and Page enabled, so console errors, exceptions and
    HTTP statuses are recorded from the first request); ``js`` / ``wait`` evaluate; ``errors`` / ``http_errors`` read what the
    session saw; ``mark`` / ``since`` cut the record per step; ``screenshot`` writes a PNG; ``close`` ends the browser and removes
    its profile folder."""

    def __init__(self, exe: str, width: int = 1440, height: int = 900, start_timeout: float = 30.0):
        self.dir = tempfile.mkdtemp(prefix="fs1-cdp-profile-")
        self.width, self.height = int(width), int(height)
        self.proc = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.dir}", "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                                      "--disable-component-update", "--disable-sync", "--disable-gpu", "--mute-audio", "--disable-default-apps",
                                      "--renderer-process-limit=2", f"--window-size={self.width},{self.height}", "about:blank"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(self.dir, "DevToolsActivePort")
        deadline = time.time() + start_timeout
        text = ""
        while time.time() < deadline:
            if os.path.exists(port_file):
                with open(port_file, encoding="utf-8") as f:
                    text = f.read()
                if "\n" in text:
                    break
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        if "\n" not in text:
            self._kill()
            raise RuntimeError(f"the browser did not start (exit {self.proc.poll()}): no DevToolsActivePort in {start_timeout:.0f} s")
        port, path = text.split("\n")[:2]
        self.ws = _WS(f"ws://127.0.0.1:{port.strip()}{path.strip()}")
        self.n = 0
        self.events: List[dict] = []

    # ---- the protocol ---------------------------------------------------------------------------------------------------------
    def call(self, method: str, params: Optional[dict] = None, session: Optional[str] = None, timeout: float = 30.0) -> dict:
        self.n += 1
        mid = self.n
        m = {"id": mid, "method": method, "params": params or {}}
        if session:
            m["sessionId"] = session
        self.ws.send(json.dumps(m))
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result") or {}
            if "method" in r:
                self.events.append(r)
        raise TimeoutError(method)

    def pump(self, seconds: float = 0.2) -> None:
        """Read the events that arrived in the next ``seconds`` (a cheap call keeps the socket moving)."""
        end = time.time() + seconds
        while time.time() < end:
            self.call("Browser.getVersion", timeout=10)
            time.sleep(0.05)

    def page(self, url: str = "about:blank", width: Optional[int] = None, height: Optional[int] = None) -> str:
        tid = self.call("Target.createTarget", {"url": "about:blank", "newWindow": True})["targetId"]
        sid = self.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        for m in ("Runtime.enable", "Log.enable", "Network.enable", "Page.enable"):
            self.call(m, session=sid)
        self.viewport(sid, width or self.width, height or self.height)
        if url and url != "about:blank":
            self.navigate(sid, url)
        return sid

    def viewport(self, session: str, width: int, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride", {"width": int(width), "height": int(height), "deviceScaleFactor": 1, "mobile": False}, session=session)

    def navigate(self, session: str, url: str, timeout: float = 30.0) -> None:
        self.call("Page.navigate", {"url": url}, session=session, timeout=timeout)
        self.wait(session, "document.readyState === 'complete'", timeout=timeout)

    def js(self, session: str, expr: str, timeout: float = 30.0):
        r = self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, session=session, timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError((d.get("exception") or {}).get("description") or d.get("text") or "evaluation failed")
        return (r.get("result") or {}).get("value")

    def wait(self, session: str, expr: str, timeout: float = 20.0, every: float = 0.15):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.js(session, expr)
            if last:
                return last
            time.sleep(every)
        raise TimeoutError(f"{expr} still {last!r} after {timeout}s")

    def key(self, session: str, key: str, code: Optional[str] = None, vk: int = 0, shift: bool = False) -> None:
        """One key press (keyDown + keyUp) with an optional Shift: F-keys need ``vk`` (F1 = 112 .. F12 = 123)."""
        mods = 8 if shift else 0
        base = {"key": key, "code": code or key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        self.call("Input.dispatchKeyEvent", dict(base, type="keyDown"), session=session)
        self.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), session=session)

    def screenshot(self, session: str, path: str, full: bool = False) -> str:
        params: Dict = {"format": "png"}
        if full:
            dims = self.js(session, "[document.documentElement.scrollWidth, document.documentElement.scrollHeight]") or [self.width, self.height]
            params.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": int(dims[0]), "height": int(min(dims[1], 6000)), "scale": 1})
        data = self.call("Page.captureScreenshot", params, session=session, timeout=60)["data"]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    # ---- what the page saw ------------------------------------------------------------------------------------------------------
    def mark(self) -> int:
        """A cut in the event record: ``errors(since=mark)`` reads only what came after it."""
        self.pump(0.05)
        return len(self.events)

    def _events(self, session: Optional[str], since: int) -> List[dict]:
        return [e for e in self.events[since:] if session is None or e.get("sessionId") == session]

    def errors(self, session: Optional[str] = None, since: int = 0) -> List[str]:
        """Uncaught exceptions, console.error calls and browser log errors (a failed resource load is one) seen so far."""
        out = []
        for e in self._events(session, since):
            m, p = e.get("method"), e.get("params") or {}
            if m == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails") or {}
                out.append("exception: " + str((d.get("exception") or {}).get("description") or d.get("text")))
            elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
                out.append("console: " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif m == "Log.entryAdded" and (p.get("entry") or {}).get("level") == "error":
                en = p["entry"]
                out.append(f"log: {en.get('text')} {en.get('url') or ''}".strip())
        return out

    def responses(self, session: Optional[str] = None, since: int = 0) -> List[dict]:
        """Every HTTP response the page received: ``{url, status, type}``."""
        out = []
        for e in self._events(session, since):
            if e.get("method") == "Network.responseReceived":
                p = e.get("params") or {}
                r = p.get("response") or {}
                out.append({"url": r.get("url"), "status": int(r.get("status") or 0), "type": p.get("type")})
        return out

    def http_errors(self, session: Optional[str] = None, since: int = 0) -> List[str]:
        """Responses with a status >= 400 and requests that failed outright (cancelled navigations of the page's own polls excluded)."""
        out = [f"{r['status']} {r['url']}" for r in self.responses(session, since) if r["status"] >= 400]
        for e in self._events(session, since):
            if e.get("method") == "Network.loadingFailed":
                p = e.get("params") or {}
                if not p.get("canceled"):
                    out.append(f"failed {p.get('errorText')} {p.get('type')}")
        return out

    # ---- the end ----------------------------------------------------------------------------------------------------------------
    def _kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def close(self) -> None:
        try:
            self.call("Browser.close", timeout=5)
        except Exception:  # noqa: BLE001 - the browser may already be gone
            pass
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for _ in range(20):                      # the profile folder unlocks a moment after the process exits
            shutil.rmtree(self.dir, ignore_errors=True)
            if not os.path.exists(self.dir):
                break
            time.sleep(0.25)


__all__ = ["Browser", "find_browser", "NO_BROWSER", "CANDIDATES"]
