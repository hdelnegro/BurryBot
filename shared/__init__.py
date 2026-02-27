"""
shared/ — Platform-agnostic core library for BurryBot.

Modules here are imported by all agent directories (polymarket_agent/,
kalshi_agent/, etc.) via sys.path manipulation rather than pip install.

Usage in any agent's file:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from shared.portfolio import Portfolio
"""
