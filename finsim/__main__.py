"""``python -m finsim`` from the repository root ``C:\\Users\\logan\\intelligence-terminal`` (STARTUP-01, FINSIM1_PLAN P7 task 1).

Why the repo-root launch failed: this folder (``...\\intelligence-terminal\\finsim``) holds the ``finsim`` PACKAGE
(``finsim\\finsim\\__init__.py``) and FinSim2 (``finsim\\finsim2``) but is itself not a package (no ``__init__.py``).
From the repository root, ``import finsim`` therefore found THIS folder as a namespace package with no ``__main__``, and
Python stopped with "No module named finsim.__main__; 'finsim' is a package and cannot be directly executed".

This file is that missing ``__main__``.  It puts its own folder first on ``sys.path``, forgets the namespace package
(every ``finsim`` / ``finsim.*`` entry of ``sys.modules``), imports the real package from here and runs FinSim1's
command line, so both of these work and do the same thing:

    cd C:\\Users\\logan\\intelligence-terminal; python -m finsim serve
    cd C:\\Users\\logan\\intelligence-terminal\\finsim; python -m finsim serve      (the inner package, unchanged)

(``python finsim`` from the repository root runs this file too.)  Nothing is pip-installed (``pyproject.toml``'s
``finsim*`` would also capture ``finsim2``), FinSim2 is untouched (``python -m finsim2`` imports its own package), and
``finsim\\finsim\\app.py`` - which FinSim2's launcher imports - is not involved.  Standard library only.
"""
import os
import sys


def _run() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)                           # ...\intelligence-terminal\finsim first: the real finsim package
    for name in [n for n in sys.modules if n == "finsim" or n.startswith("finsim.")]:
        del sys.modules[name]                          # the namespace package this folder was imported as
    from finsim.terminal.app import main
    return main()


if __name__ == "__main__":
    sys.exit(_run())
