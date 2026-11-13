"""The shared result container `goal_executor.py` and `diagnostics.py` build up and return."""
from dataclasses import dataclass, field


@dataclass
class Execution:
    outcome: str = "INCONCLUSIVE"
    classification: str = "execution_failure"
    observed_behavior: str = "Execution did not complete."
    logs: list = field(default_factory=list)
    media: list = field(default_factory=list)
