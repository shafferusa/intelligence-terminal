"""Run FinSim1's test suite (every ``tests/test_terminal_*.py``) and print one summary line (TEST-08, FINSIM1_PLAN P7 task 10).

    python tests\\run_terminal_suite.py                      from C:\\Users\\logan\\intelligence-terminal\\finsim (any folder works)
    py -3.14 tests\\run_terminal_suite.py                    the same suite on Python 3.14
    python tests\\run_terminal_suite.py test_terminal_app    only the named files (with or without "tests." / ".py")
    python tests\\run_terminal_suite.py --no-swaps           leave out tests/test_terminal_swaps*.py

The last lines it prints are, in this order:

    RAN <tests> FAIL <failures> ERR <errors> SKIP <skipped> FILES <test files>
    ResourceWarnings <n>

and it exits 0 when every test passed (skips allowed), 1 otherwise.  It carries the logic of the orchestrator's
``run_suite.py`` (the same files, the same order, the same loader and runner) and runs from the folder that holds the
``finsim`` package whatever the current folder is, so the tests import this tree's ``finsim`` and ``tests`` packages.
Warnings use the "default" filter (each shown once per place) unless ``-W`` is given, as ``python -m unittest`` does;
``ResourceWarnings`` counts the ResourceWarning messages (an unclosed file, socket or database) shown while the tests
ran - they are printed as usual and the count is the evidence for the "no ResourceWarnings" check (TEST-09).
FinSim2's tests (``test_fs2_*``, ``test_desk_*``) are not part of this suite.  Standard library only.
"""
from __future__ import annotations

import glob
import os
import sys
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
FINSIM_DIR = os.path.dirname(HERE)          # ...\intelligence-terminal\finsim: holds the finsim and tests packages
PATTERN = "test_terminal_*.py"


def test_files(selected=(), no_swaps: bool = False) -> list:
    """The module names to run (``tests.test_terminal_x``), sorted, from ``tests/test_terminal_*.py`` of this tree."""
    names = []
    want = {os.path.basename(s).removesuffix(".py").removeprefix("tests.") for s in selected}
    for path in sorted(glob.glob(os.path.join(HERE, PATTERN))):
        base = os.path.basename(path)[:-3]
        if no_swaps and base.startswith("test_terminal_swaps"):
            continue
        if want and base not in want:
            continue
        names.append("tests." + base)
    unknown = sorted(want - {n[len("tests."):] for n in names})
    if unknown:
        raise SystemExit(f"no such test file under {HERE}: {', '.join(unknown)}")
    return names


class _ResourceWarningCounter:
    """Wraps ``warnings.showwarning``: counts every ResourceWarning shown, then shows it as Python would."""

    def __init__(self):
        self.count = 0
        self._orig = warnings.showwarning

    def __call__(self, message, category, filename, lineno, file=None, line=None):
        if isinstance(category, type) and issubclass(category, ResourceWarning):
            self.count += 1
        return self._orig(message, category, filename, lineno, file, line)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    no_swaps = "--no-swaps" in args
    selected = [a for a in args if not a.startswith("-")]
    os.chdir(FINSIM_DIR)                     # the tests find their fixtures and the repo files from this folder
    if sys.path and os.path.normcase(os.path.abspath(sys.path[0] or ".")) == os.path.normcase(HERE):
        sys.path[0] = FINSIM_DIR             # the script's folder (tests\) out, this tree's finsim folder in
    elif FINSIM_DIR not in sys.path:
        sys.path.insert(0, FINSIM_DIR)
    names = test_files(selected, no_swaps)
    counter = _ResourceWarningCounter()
    warnings.showwarning = counter
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        # "default" (each warning shown once per place) unless -W was given: what `python -m unittest` does, so ResourceWarnings
        # are visible and counted (a bare TextTestRunner leaves Python's filters, which ignore them)
        result = unittest.TextTestRunner(verbosity=1, warnings=None if sys.warnoptions else "default").run(suite)
    finally:
        warnings.showwarning = counter._orig
    print(f"RAN {result.testsRun} FAIL {len(result.failures)} ERR {len(result.errors)} SKIP {len(result.skipped)} FILES {len(names)}"
          + (" (swap tests excluded)" if no_swaps else ""), flush=True)
    print(f"ResourceWarnings {counter.count}", flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
