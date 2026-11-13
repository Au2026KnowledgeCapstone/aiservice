"""Engine tunables with no envelope-driven config channel.

The reference reads these from `settings()` (scenario_timeout_seconds,
max_actions, ai_vision_enabled). engines/ has no config object to read from —
see the A4 handoff notes on the config-shape decision — so they are fixed
constants here instead.
"""
SCENARIO_TIMEOUT_SECONDS = 180
MAX_ACTIONS = 15
AI_VISION_ENABLED = True
