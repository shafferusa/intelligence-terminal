"""FinSim2: the portfolio-manager edition of FinSim.

One job (portfolio manager), one choice at the start (how much to run), a clean single-book interface, and an analytics
workspace: the equation library, a scoreboard of every tradeable asset, and the slot for the Shaffer Score. It runs on
FinSim's engine unchanged (same markets, same accounting), keeps its saves apart (~/.finsim2) and serves its own UI.
"""
APP_NAME = "FinSim2"
DEFAULT_PORT = 8865
