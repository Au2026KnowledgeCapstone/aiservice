"""Pure URL-comparison helpers shared across action compilation, checkpoints and exploration.

`destination_matches` is defined in the reference inside checkpoints.py (Step
4), but action_contracts.py (Step 3) needs it already via
`observed_navigation_control`. Factored out here so neither step has to
duplicate it or forward-reference the other.
"""
from urllib.parse import urlsplit

from engines.mvp.policy import origin


def destination_matches(expected, actual):
    try:
        a, b = urlsplit(expected), urlsplit(actual)
        return (
            origin(a._replace(fragment="").geturl()) == origin(b._replace(fragment="").geturl())
            and a.path.rstrip("/") == b.path.rstrip("/")
            and (not a.query or a.query == b.query)
            and (not a.fragment or a.fragment == b.fragment)
        )
    except ValueError:
        return False
