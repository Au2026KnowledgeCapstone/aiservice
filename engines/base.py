from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

Verdict = Literal["pass", "fail", "inconclusive"]

Classification = Literal[
    "criteria_satisfied",
    "criteria_violated",
    "locator_failure",
    "environment_failure",
    "execution_error",
]


@dataclass(frozen=True)
class Target:
    url: str
    viewport_width: int
    viewport_height: int


@dataclass(frozen=True, repr=False)
class Credentials:
    """Decrypted only inside the worker process. Never logged, never stored."""
    values: dict[str, str]

    def __repr__(self) -> str:
        return f"Credentials(values=<redacted {len(self.values)} items>)"


@dataclass(frozen=True)
class CriterionSnapshot:
    id: str
    text: str


@dataclass(frozen=True)
class RequirementSnapshot:
    """Immutable copy taken when the run was queued."""
    id: str
    title: str
    description: str
    criteria: Sequence[CriterionSnapshot]


@dataclass(frozen=True)
class CriterionVerdict:
    criterion_id: str
    verdict: Verdict
    rationale: str
    cited_evidence_ids: Sequence[str]
    source: Literal["ai", "objective", "engine"]


@dataclass(frozen=True)
class ActionRecord:
    index: int
    kind: str
    description: str
    outcome: Literal["completed", "stopped"]
    evidence_id: str | None = None


@dataclass(frozen=True)
class RequirementOutcome:
    verdict: Verdict
    classification: Classification
    observed_behavior: str
    criterion_verdicts: Sequence[CriterionVerdict]
    actions: Sequence[ActionRecord]


class EvidenceSink(Protocol):
    """Engines emit evidence through this; they never choose a storage path."""

    async def put_screenshot(
        self, data: bytes, *, step_index: int | None = None
    ) -> str: ...

    async def put_text(self, body: str, *, kind: str) -> str: ...


class AIProvider(Protocol):
    async def structured(
        self,
        *,
        operation: str,
        system: str,
        user: str,
        schema: dict,
        images: Sequence[bytes] = (),
    ) -> dict: ...


class Session(Protocol):
    async def close(self) -> None: ...


class Engine(Protocol):
    name: str

    async def prepare(self, target: Target, credentials: Credentials) -> Session: ...

    async def survey(
        self,
        session: Session,
        requirements: Sequence[RequirementSnapshot],
    ) -> None:
        """Optional once-per-run reconnaissance, before any requirement runs.

        The MVP engine does not implement this — its reconnaissance is
        per-requirement and lives inside `execute`. It exists for an engine
        that must map the application once and amortise that across every
        requirement in the run. Defining it now is cheap; adding it later
        changes the worker's run loop, which is not.
        """
        return None

    async def execute(
        self,
        session: Session,
        requirement: RequirementSnapshot,
        sink: EvidenceSink,
    ) -> RequirementOutcome: ...

    async def close(self) -> None: ...


class BaseEngine:
    """Mixin providing the no-op `survey` default; engines override only what they need."""

    async def survey(
        self,
        session: Session,
        requirements: Sequence[RequirementSnapshot],
    ) -> None:
        return None
