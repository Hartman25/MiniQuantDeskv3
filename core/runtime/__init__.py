"""
Runtime package - app runners and shared boot logic.

This is the "application layer" (LEAN/Freqtrade/Hummingbot style):
- Shared boot/run loop
- Mode selection (paper/live)
- Wiring: config -> container -> broker -> strategies -> loop
"""
