# FinSim Live Terminal — runbook (launcher, CLI, installer; WP-14)

Author: Claude (Fable 5.1), 2026-10-20. Companion of `TERMINAL_DESIGN.md` §16 (server) and §18 (launcher). Everything
below is `python -m finsim <command>` run **from `C:\Users\logan\intelligence-terminal\finsim`** with `py -3.12`
(the launcher writes the 3.12 `pythonw.exe` found by `py -3.12` into the Startup entry; 3.14 runs the tests too).
Code: `finsim/finsim/terminal/app.py`; entry point `finsim/finsim/__main__.py` (re-pointed to the terminal).

## 1. The final PowerShell sequence (cut over from the 0.11.0 engine)

```powershell
cd C:\Users\logan\intelligence-terminal\finsim
py -3.12 -m finsim stop                 # stops the OLD 0.11.0 engine too (same /api/shutdown route); prints the finsim.db sha256
py -3.12 -m finsim install --no-open    # replaces Startup\FinSim Server.vbs, removes the "FinSim Server" task, starts 1.0.0
py -3.12 -m finsim status               # must show finsim-terminal 1.0.0, integrity ok, the Startup entry, finsim.db unchanged
py -3.12 -m finsim open
```

What to expect, line by line:

1. `stop` — "stopping a non-terminal server (engine 0.11.0) on http://127.0.0.1:8765/ … the port is quiet … stopped",
   then `finsim.db sha256 <hex>`. Exit 0. (Exit 1 = nothing was running, which is also fine; exit 2 = still alive,
   see §5.) The old engine checkpoints its own write-ahead log when it closes, so the hash printed here is the
   stable one.
2. `install --no-open` — one line per step, in this order: `sha256 …finsim.db = <hex> (baseline written to
   …\finsim.db.sha256)` → `nothing answers on …` (or the old engine is stopped) → `scheduled task 'FinSim Server':
   removed|not present` → `backed up …` (the old Startup entry and the old `finsim-server.vbs`, `finsim-open.vbs`,
   `make-shortcuts.vbs` go to `%USERPROFILE%\.finsim\backups\*.<stamp>.bak`) → `removed old …` → `wrote …\FinSim
   Server.vbs`, `…\FinSim Terminal.vbs`, `…\finsim.ico` → `Startup entry …\Startup\FinSim Server.vbs (replaced the old
   entry of that name)` → `shortcuts: …\Desktop\FinSim.lnk, …\Programs\FinSim.lnk` → `starting through …\FinSim
   Server.vbs` → `server: finsim-terminal 1.0.0 is up at http://127.0.0.1:8765/` → the full `status` block → this
   PowerShell sequence → `sha256 …finsim.db = <hex> (unchanged)`. Exit 0 when the terminal answers at the end, 1
   otherwise (the end of `terminal.log` is printed).
3. `status` — exit 0 only when a server **with the `finsim-terminal` marker** answers. Check: `server: running
   finsim-terminal 1.0.0`, no "restart to serve the new code" warning, `integrity: ok`, every worker `alive`, the
   providers `ok`, `lock: held by pid <n>`, `Startup entry: present (…\Startup\FinSim Server.vbs)`, `finsim.db:
   sha256 … (unchanged)`.
4. `open` — `http://127.0.0.1:8765/ -> app window (chrome.exe|msedge.exe|brave.exe)` (fallback: the default
   browser). The window uses its own profile `%USERPROFILE%\.finsim\terminal-window-profile`.

Verify afterwards (read-only): `Get-FileHash $env:USERPROFILE\.finsim\finsim.db -Algorithm SHA256` equals the hash in
`%USERPROFILE%\.finsim\finsim.db.sha256`; `Get-Content "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\FinSim
Server.vbs"` contains `"finsim-terminal"` and `-m finsim serve --port 8765`; `schtasks /Query /TN "FinSim Server"`
reports that the task does not exist.

## 2. Commands and exit codes (§18)

| command | behaviour | exit |
|---|---|---|
| `serve [--port 8765] [--home DIR] [--db FILE] [--host 127.0.0.1] [--offline] [--log-level INFO]` | foreground server (`finsim.terminal.server.serve`). `--db …\finsim.db` is refused (the old saves are never the terminal's database); `--offline` is refused on the default home or with `FINSIM_TERMINAL_DB` set; `--host` must be loopback | 0 ok; 3 another instance / port; 4 newer schema; 5 startup error or refusal (traceback in `terminal.log`) |
| `stop [--timeout 15] [--force]` | `POST /api/shutdown` (the old engine answers the same route); waits for the port to go quiet and the lock to release; judges the lock-file PID with `OpenProcess` + `GetExitCodeProcess == 259` + the image name; `--force` → `taskkill /PID` only when it is a live **python** image; prints `finsim.db sha256` | 0 stopped; 1 nothing running; 2 still alive |
| `status` | served version vs the code on disk ("restart to serve the new code"), the marker ("a non-terminal server answers on 8765"), pid, uptime, db, log tail when down or unhealthy, lock, workers, providers, integrity, lifecycle, Startup entry, backups, `sha256(finsim.db)` vs the install baseline | 0 running; 1 not running |
| `install [--no-open]` | the §18 order: sha256 baseline → stop the old engine → delete the `FinSim Server` task → back up + remove the old VBS files → write the new files → Startup copy → shortcuts → start → status → sequence → sha256 again | 0 the terminal is up; 1 otherwise |
| `open` | opens the window; when the terminal does not answer it starts the server through `FinSim Server.vbs` first (the VBS asks a foreign server to shut down); never a second server (a duplicate exits 3, which `open` reports with the log tail) | 0 / 1 |
| `uninstall [--purge]` | removes the Startup entry, shortcuts, VBS files, icon and window profile; stops the terminal; `--purge` deletes `terminal.db` only after you type its full path (`finsim.db` can never be named) | 0 |
| `legacy [--port 8766] [--refresh]` | copies `finsim.db` to `%USERPROFILE%\.finsim\legacy\legacy-view.db` with the sqlite3 backup API (once; `--refresh` re-copies) and runs the OLD `finsim.api.server` on the **copy**; the archive view — anything saved there stays in the copy | 0; 1 no `finsim.db` |

## 3. Where things live

| path | what |
|---|---|
| `%USERPROFILE%\.finsim\terminal.db` (+ `-wal`, `-shm`) | the terminal's database (`FINSIM_TERMINAL_DB` overrides; `FINSIM_HOME` moves the home) |
| `%USERPROFILE%\.finsim\finsim.db` | the OLD simulation's saves — never opened for writing by the terminal (hashed only; `legacy` reads it as a backup source) |
| `%USERPROFILE%\.finsim\finsim.db.sha256` | the install baseline (`<sha256>  <path>`) that `status` compares against |
| `%USERPROFILE%\.finsim\terminal.log` | rotating log (5 MB × 3); `status` prints the last 15 lines when the server is down or unhealthy |
| `%USERPROFILE%\.finsim\launcher.log` | what a window-less `open` (from the shortcut) could not say on a console |
| `%USERPROFILE%\.finsim\terminal.lock` | `{pid, port, started_ts, version}`, byte-locked by the live server |
| `%USERPROFILE%\.finsim\FinSim Server.vbs`, `FinSim Terminal.vbs`, `finsim.ico` | the launcher files `install` writes (ASCII) |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FinSim Server.vbs` | the login entry (a copy of the server VBS; same name as the old entry, which it replaced) |
| `Desktop\FinSim.lnk`, `…\Start Menu\Programs\FinSim.lnk` | `wscript "FinSim Terminal.vbs"` → `pythonw -m finsim open` |
| `%USERPROFILE%\.finsim\backups\` | the terminal's own `.db` snapshots **and** the `*.<stamp>.bak` copies of the old launcher files (for the rollback) |
| `%USERPROFILE%\.finsim\legacy\` | the `legacy` copy and the old server's own snapshot rotation (kept out of `backups\`) |
| `%USERPROFILE%\.finsim\terminal-window-profile\` | the app window's browser profile |

The served process is exactly `pythonw.exe -m finsim serve --port 8765` with `PYTHONPATH` and `FINSIM_HOME` set by the
VBS; the shared-machine capture guard exempts command lines matching `-m finsim serve … --port 8765`
(`TERMINAL_DECISIONS.md` §17). The server never spawns child processes; only `open` spawns the browser.

## 4. Rollback (back to the 0.11.0 engine on 8765)

```powershell
cd C:\Users\logan\intelligence-terminal\finsim
py -3.12 -m finsim uninstall                      # Startup entry, shortcuts, VBS files, window profile gone; the terminal stopped; terminal.db kept
# restore the old launcher files from the backups install made (pick the newest stamp):
$b = "$env:USERPROFILE\.finsim\backups"
Copy-Item "$b\Startup FinSim Server.vbs.<stamp>.bak" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\FinSim Server.vbs"
Copy-Item "$b\finsim-server.vbs.<stamp>.bak" "$env:USERPROFILE\.finsim\finsim-server.vbs"
Copy-Item "$b\finsim-open.vbs.<stamp>.bak"   "$env:USERPROFILE\.finsim\finsim-open.vbs"
Copy-Item "$b\make-shortcuts.vbs.<stamp>.bak" "$env:USERPROFILE\.finsim\make-shortcuts.vbs"
```

Then **one of** the following, because the old Startup entry runs `pythonw -m finsim serve --db …\finsim.db --host
127.0.0.1 --port 8765` and `python -m finsim` now dispatches to the terminal, which refuses `--db finsim.db` (exit 5):

- restore the old entry point: `git checkout -- finsim/finsim/__main__.py` (the old CLI; `demo`/`phone` come back,
  `legacy` goes away), then `wscript "$env:USERPROFILE\.finsim\finsim-server.vbs"` or sign out and in; or
- keep the new CLI and start the old engine directly:
  `py -3.12 -c "from finsim.api.server import serve; serve(r'C:\Users\logan\.finsim\finsim.db', '127.0.0.1', 8765)"`
  (foreground; the old engine backs `finsim.db` up into `.finsim\backups` as it always did).

`finsim.db` itself needs no rollback: the terminal never writes it (`status` proves it with the sha256 line). The
terminal's `terminal.db` survives `uninstall` and is only deleted by `uninstall --purge` after typing its path.

## 5. Troubleshooting

- **`status` says "a non-terminal server answers on 8765"** — the old engine came back (its Startup entry or task
  was restored). `stop`, then `install --no-open` (which removes both again).
- **"restart to serve the new code"** — the running process was started from older files. `stop` then `open`.
- **`stop` exits 2 "still alive: pid N (…pythonw.exe) holds the lock; pass --force"** — the process ignored
  `/api/shutdown` (a command in flight, or a hung worker). `stop --force` runs `taskkill /PID N /F` **only** when
  `OpenProcess` says the pid is alive **and** its image name contains `python`; a pid that is alive under another
  image (PID reuse) is never killed — check Task Manager instead.
- **`stop` exits 2 "byte-locked by a live process whose pid cannot be read"** — a live server holds the lock but its
  JSON could not be read; wait a few seconds and retry; `status` shows the lock line.
- **`lock: STALE`** — the server died and left `terminal.lock` behind; the OS released the byte lock, so a new
  `open` simply takes over (the file is rewritten). Nothing to clean by hand.
- **`open` reports "exited with code 3/4/5"** — 3: another instance already owns the home or the port is taken
  (`status`); 4: the database was written by a newer schema (use the newer code); 5: startup error — the traceback
  is the end of `terminal.log` (printed by `open` and `status`).
- **`serve --offline` is refused** — pass `--home <scratch folder>` and make sure `FINSIM_TERMINAL_DB` is unset:
  fake prices never reach the real book.
- **`install` printed "finsim.db changed while the old engine shut down … baseline re-taken"** — the old engine
  folded its write-ahead log into `finsim.db` as it closed (its normal shutdown); the baseline was taken again after
  that, so the final "unchanged" verdict is about the terminal only. The old hash is still in the console output.
- **"CHANGED - investigate before trading"** at the end of `install`, or "CHANGED since the install baseline" in
  `status` — something other than the old engine's own shutdown wrote `finsim.db` after the baseline. Do not trade
  until you know what (the terminal has no code path that opens it for writing; look for a second old-engine process
  or a `legacy` run against the live path, which `legacy` refuses by construction).
- **The VBS does nothing at login** — it quits silently when `pythonw.exe` or `finsim\terminal\__init__.py` is not
  at the paths written into it (`type "%USERPROFILE%\.finsim\FinSim Server.vbs"`); rerun `install --no-open` after
  moving Python or the repo. The paths must be ASCII (the installer refuses otherwise).
- **Two VBS launches at login** — harmless: the single-instance lock and the exclusive port bind make the second
  server exit 3; the probe in the VBS is a courtesy, not the guard.

## 6. Shared-laptop notes

Run one `py -3.12 -m unittest tests.test_terminal_app` process at a time; the tests bind only port 0, never 8765 or
8766, never touch `~\.finsim` (they set `FINSIM_HOME` / `FINSIM_TERMINAL_DB` to a temp folder) and never start the
server (the starter is injected). The market-data capture session on this machine is exempt from nothing the
launcher does: `install`, `open` and `stop` touch only the terminal (and, on request, the old 0.11.0 engine).
