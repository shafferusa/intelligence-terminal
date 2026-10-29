# FinSim Terminal (FinSim1) - runbook (launcher, CLI, installer)

Companion of `TERMINAL_DESIGN.md` §16 (server) and §18 (launcher); updated for release 1.0.6 (FINSIM1_PLAN package P7).
Everything below is `python -m finsim <command>`. It works from `C:\Users\logan\intelligence-terminal\finsim` (the inner
package) and from `C:\Users\logan\intelligence-terminal` (the outer shim `finsim\__main__.py`), and `FinSim.cmd` in either
folder does the same (no command = `open`; PowerShell: `.\FinSim.cmd serve`). `python` here is 3.12.10 (`py -3.12` is the
same interpreter; `py` / `python3` 3.14.5 also work); the launcher writes the 3.12 `pythonw.exe` found by `py -3.12` into
the Startup entry. Code: `finsim/finsim/terminal/app.py`; entry points `finsim/finsim/__main__.py` and `finsim/__main__.py`.

## 1. Installing a release (the deploy path)

The installed app runs from a release folder made from a commit, never from the repository:
`%USERPROFILE%\.finsim\releases\<tag>\intelligence-terminal\finsim`, with `RELEASE.txt` two folders up (tag, source
commit SHA, branch, VERSION, time). The release tooling installs it; by hand, from that folder:

```powershell
cd $env:USERPROFILE\.finsim\releases\<tag>\intelligence-terminal\finsim
py -3.12 -m finsim stop                 # stops the running terminal; prints the finsim.db sha256
py -3.12 -m finsim install --no-open    # Startup\FinSim Server.vbs and the FinSim icon now start this folder; starts it
py -3.12 -m finsim status               # finsim-terminal <VERSION>, integrity ok, "Startup entry -> <this folder> (...)", finsim.db unchanged
py -3.12 -m finsim open
```

`install` run from a git working tree (the repository) is refused, nothing changed, unless the owner passes
`--from-working-tree` (owner rule: no deploy without the owner's flag).

What to expect, line by line:

1. `stop` - "stopping finsim-terminal \<old\> on http://127.0.0.1:8765/ ... the port is quiet ... stopped", then
   `finsim.db sha256 <hex>`. Exit 0 (1 = nothing was running, also fine; 2 = still alive, see §5).
2. `install --no-open` - one line per step, in this order: `sha256 ...finsim.db = <hex> (baseline written to
   ...\finsim.db.sha256)` -> `nothing answers on ...` -> `scheduled task 'FinSim Server': removed|not present` -> `backed up
   ...` (the old Startup entry to `%USERPROFILE%\.finsim\backups\*.<stamp>.bak`) -> `wrote ...\FinSim Server.vbs`,
   `...\FinSim Terminal.vbs`, `...\finsim.ico` -> `Startup entry ...\Startup\FinSim Server.vbs (replaced the old entry of
   that name)` -> `shortcuts: ...\Desktop\FinSim.lnk, ...\Programs\FinSim.lnk`, followed by a NOTE for each Chrome app
   shortcut named FinSim (left in place) -> `starting through ...\FinSim Server.vbs (the code in <folder>)` -> `server:
   finsim-terminal <VERSION> is up at http://127.0.0.1:8765/` -> the full `status` block -> the PowerShell sequence for this
   folder -> `sha256 ...finsim.db = <hex> (unchanged)`. Exit 0 when the terminal answers at the end, 1 otherwise or when
   refused.
3. `status` - exit 0 only when a server **with the `finsim-terminal` marker** answers. Check: `server: running
   finsim-terminal <VERSION>`, `code: <folder>`, no NOTE or WARNING under it, `integrity: ok`, every worker `alive`, the
   providers `ok` or `idle`, `lock: held by pid <n>`, `Startup entry: present (...)`, `Startup entry -> <folder>
   (finsim-terminal <VERSION>, release <tag>, commit <SHA>)`, `its pythonw: ... (present)`, `FinSim icon: ...`,
   `finsim.db: sha256 ... (unchanged)`.
4. `open` - `http://127.0.0.1:8765/ -> app window (chrome.exe|msedge.exe|brave.exe)` (fallback: the default browser),
   with its own profile `%USERPROFILE%\.finsim\terminal-window-profile`.

Verify afterwards (read-only): `Get-Content "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\FinSim Server.vbs"`
names `repo = "<the release folder>"` and contains `"finsim-terminal"` and `-m finsim serve --port 8765`; `schtasks /Query
/TN "FinSim Server"` reports that the task does not exist.

## 2. Commands and exit codes (§18)

| command | behaviour | exit |
|---|---|---|
| (none) | prints a three-line hint: `serve`, `open`, `status` | 0 |
| `serve [--port 8765] [--home DIR] [--db FILE] [--host 127.0.0.1] [--offline] [--log-level INFO]` | foreground server (`finsim.terminal.server.serve`), after one line naming the code folder, VERSION and database (on the default home it adds which folder the FinSim icon and Windows login start when that is another one). `--db ...\finsim.db` is refused; `--offline` is refused on the default home or with `FINSIM_TERMINAL_DB` set; `--host` must be loopback | 0 ok; 3 another instance (version, PID, since \<ET time\>, URL, code folder) or the port is taken; 4 newer schema; 5 startup error or refusal (traceback in `terminal.log`) |
| `stop [--timeout 15] [--force]` | `POST /api/shutdown`; waits for the port to go quiet and the lock to release; judges the lock-file PID with `OpenProcess` + `GetExitCodeProcess == 259` + the image name; `--force` -> `taskkill /PID` only when it is a live **python** image; prints `finsim.db sha256` | 0 stopped; 1 nothing running; 2 still alive |
| `status` | what runs: version, pid, uptime, the code folder (the lock's `code`), integrity, workers, providers, lifecycle, lock; the marker ("a non-terminal server answers on 8765"); the two-tree advice (below); the Startup entry and `Startup entry -> <folder> (VERSION, release, commit SHA)` with whether its `pythonw.exe` and code exist; the FinSim icon; Chrome app shortcuts named FinSim; backups; `sha256(finsim.db)` vs the install baseline; the log tail when down or unhealthy | 0 running; 1 not running |
| `install [--no-open] [--from-working-tree]` | refused from a git working tree without `--from-working-tree`. The §18 order: sha256 baseline -> stop an old engine -> delete the `FinSim Server` task -> back up + remove the old VBS files -> write the new files -> Startup copy -> shortcuts (+ the Chrome app shortcut notes) -> start -> status -> sequence -> sha256 again | 0 the terminal is up; 1 otherwise or refused |
| `open` | opens the window; when the terminal does not answer it starts the server through `FinSim Server.vbs` first (it names the folder it starts); never a second server (a duplicate exits 3, which `open` reports with the log tail) | 0 / 1 |
| `uninstall [--purge]` | removes the Startup entry, shortcuts, VBS files, icon and window profile; stops the terminal; `--purge` deletes `terminal.db` only after you type its full path (`finsim.db` can never be named) | 0 |
| `import ...` | `finsim.terminal.importer.main(argv)` (C18): `--batch FILE --home DIR --mode report\|dry-run\|apply\|restore [--fetch-data-pack] [--pack FILE] [--work-dir DIR] [--dryrun DIR] [--owner-go TEXT] [--backup FILE] [--json FILE]`; "not in this build" when the module is missing | 0 done; 1 failed; 2 refused / usage |

`legacy` (the retired game's archive view of `finsim.db`) was removed from the command line in 1.0.6 (owner question Q8);
it now only says so (exit 2). Nothing was deleted: the old code and `finsim.db` stay; an existing
`%USERPROFILE%\.finsim\legacy\` copy is left as it is.

**Two code trees.** The installed app runs from the release folder; `python -m finsim` typed in the repository runs the
working tree. When the running server is another VERSION or folder than the command's own, `status` says "restart to serve
the new code (`stop` then `open`)" only when `open` would start this same folder; when the FinSim icon starts another folder
(the installed release) it prints the two real choices instead: open the installed app (`open` or the FinSim icon), or
`stop` and then `serve` here.

## 3. Where things live

| path | what |
|---|---|
| `%USERPROFILE%\.finsim\terminal.db` (+ `-wal`, `-shm`) | the terminal's database (`FINSIM_TERMINAL_DB` overrides; `FINSIM_HOME` moves the home) |
| `%USERPROFILE%\.finsim\backups\terminal-YYYYmmdd-HHMMSS.db` | the copy `Database.open` takes before migrating, at every start (newest 10 kept) |
| `%USERPROFILE%\.finsim\backups\terminal-pre-<version>-<stamp>.db` | the release installer's copy before an install |
| `%USERPROFILE%\.finsim\backups\import\terminal-pre-<batch>-<ts>.db` (+ `.sha256`) | the import's pre-apply backup (the restore procedure uses it) |
| `%USERPROFILE%\.finsim\backups\*.<stamp>.bak` | the old launcher files `install` replaced (for the rollback) |
| `%USERPROFILE%\.finsim\releases\<tag>\` | the release folders (`intelligence-terminal\finsim` + `RELEASE.txt`) |
| `%USERPROFILE%\.finsim\finsim.db` | the OLD simulation's saves - never opened for writing by the terminal (hashed only) |
| `%USERPROFILE%\.finsim\finsim.db.sha256` | the install baseline (`<sha256>  <path>`) that `status` compares against |
| `%USERPROFILE%\.finsim\terminal.log` | rotating log (5 MB x 3); `status` prints the last 15 lines when the server is down or unhealthy |
| `%USERPROFILE%\.finsim\launcher.log` | one line for each start the Startup entry or the FinSim icon could not make (pythonw or the code missing), and what a window-less `open` could not say on a console |
| `%USERPROFILE%\.finsim\terminal.lock`, `terminal.db.lock` | `{pid, port, started_ts, version, home, db, code}`, byte-locked by the live server |
| `%USERPROFILE%\.finsim\FinSim Server.vbs`, `FinSim Terminal.vbs`, `finsim.ico` | the launcher files `install` writes (ASCII; `py`, `repo`, `home` on one line) |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FinSim Server.vbs` | the login entry (a copy of the server VBS) |
| `Desktop\FinSim.lnk`, `...\Start Menu\Programs\FinSim.lnk` | the FinSim icon: `wscript "FinSim Terminal.vbs"` -> `pythonw -m finsim open` |
| `Desktop\FinSim (1).lnk`, `...\Programs\Chrome Apps\FinSim.lnk` | two old Chrome app shortcuts: they only open a window and cannot start the server; reported by `status` / `install`, deleted only on the owner's OK |
| `%USERPROFILE%\.finsim\terminal-window-profile\` | the app window's browser profile |

The served process is exactly `pythonw.exe -m finsim serve --port 8765` with `PYTHONPATH` and `FINSIM_HOME` set by the
VBS; the shared-machine capture guard exempts command lines matching `-m finsim serve ... --port 8765`
(`TERMINAL_DECISIONS.md` §17). The server never spawns child processes; only `open` spawns the browser.

## 4. Rollback

To a previous release: install that release's folder again (§1, from
`%USERPROFILE%\.finsim\releases\<older tag>\intelligence-terminal\finsim`), after checking that it can read the database
(after the IMP-F1-20261027-A import only 1.0.5 or later can; FINSIM1_PLAN D2 and section 7 "Restore"). To remove the
installed app: `py -3.12 -m finsim uninstall` (Startup entry, shortcuts, VBS files and window profile gone; the terminal
stopped; `terminal.db` kept), then restore older launcher files from `%USERPROFILE%\.finsim\backups\*.<stamp>.bak` if
wanted.

`finsim.db` itself needs no rollback: the terminal never writes it (`status` proves it with the sha256 line).

## 5. Troubleshooting

- **`status` prints "Two choices: open the installed app ... or run this tree instead"** - you ran it from a folder that is
  not the one the FinSim icon starts (typically the repository while the installed release runs). Restarting would only
  start the release again. Use the installed app, or `stop` and then `serve` in the folder you want.
- **"restart to serve the new code"** - the running process was started from older files of this same folder. `stop`
  then `open`.
- **`install` prints "install refused: ... is a git working tree"** - nothing was changed. Install from the release folder
  (§1); `--from-working-tree` is for the owner's explicit word only.
- **The FinSim icon or Windows login starts nothing** - read `%USERPROFILE%\.finsim\launcher.log` (the VBS writes why
  before it quits) and `status` (`its pythonw: ... MISSING`, `its code: MISSING`, or `Startup entry -> <folder> (MISSING
  ...)`). Install again from an existing release folder after moving Python or a release. The paths must be ASCII (the
  installer refuses otherwise).
- **`status` says "a non-terminal server answers on 8765"** - the old 0.11.0 engine came back (its Startup entry or task was
  restored). `stop`, then `install --no-open` from the release folder.
- **`stop` exits 2 "still alive: pid N (...pythonw.exe) holds the lock; pass --force"** - `stop --force` runs `taskkill /PID N
  /F` only when `OpenProcess` says the pid is alive and its image name contains `python`; a pid alive under another image
  (PID reuse) is never killed.
- **`lock: STALE`** - the server died and left `terminal.lock`; the OS released the byte lock, so a new `open` takes over.
- **`serve` / `open` exit 3** - another instance owns the home (the message names its version, PID, start time and code
  folder: use `open`) or another program holds the port (`status`, or `--port`); 4: the database was written by a newer
  release; 5: startup error - the traceback is the end of `terminal.log`.
- **`serve --offline` is refused** - pass `--home <scratch folder>` and make sure `FINSIM_TERMINAL_DB` is unset: fake prices
  never reach the real book.
- **`python -m finsim` from the repository root says "No module named finsim.__main__"** - the release or checkout lacks
  `finsim\__main__.py` (1.0.6 and later carry it); run it from `finsim\` instead.
- **Two VBS launches at login** - harmless: the single-instance lock and the exclusive port bind make the second server
  exit 3.

## 6. Tests

`python tests\run_terminal_suite.py` (and `py -3.14 tests\run_terminal_suite.py`) from `finsim\`, or from any folder with
the script's full path: every `tests\test_terminal_*.py`, ending with `RAN n FAIL n ERR n SKIP n FILES n` and
`ResourceWarnings n`. The launcher tests (`test_terminal_app`, `test_terminal_ui_ops`, `test_terminal_launch`) bind only
port 0, never 8765 or 8865, never touch `%USERPROFILE%\.finsim` and never execute a launcher VBS or registration code;
`test_terminal_launch` starts real servers as child processes on temp homes with the fake price source and stops each one
over loopback.

## 7. Historical import: checking an applied batch

The apply and restore procedures of an import batch are in FINSIM1_PLAN section 7 ("Apply procedure", "Restore"); `import ...` (§2) runs the import tool.

Checking an applied import (any time, read-only, also while the terminal runs): `py -3.12 -m finsim import --batch finsim\terminal\data\imports\IMP-F1-20261027-A.jsonl --home "%USERPROFILE%\.finsim" --mode report`. The expected result is lines 1-21 present_differs (preserved, never corrected; lines added since show as differences), 22-23 present_match, 24-33 already_imported, 34 declined and 35 unresolved. A dry run of an applied batch on a copy (before 16:00 ET) finds every import line already in the book. It only checks and reports: 0 entries, no command, the batch's record untouched, and its report in the dry run's work folder. The same view is on screen at History > Imports, with 'Check against the book now'. Restore after an apply needs the pre-import backup and a 1.0.5 or later release (FINSIM1_PLAN section 7, "Restore").
