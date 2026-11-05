import pytest

from engines.base import CriterionSnapshot, RequirementSnapshot
from engines.stub.engine import StubEngine


class _FakeSink:
    def __init__(self):
        self.issued: list[str] = []

    async def put_screenshot(self, data, *, step_index=None):
        raise NotImplementedError

    async def put_text(self, body, *, kind):
        evidence_id = f"evidence-{len(self.issued)}"
        self.issued.append(evidence_id)
        return evidence_id


@pytest.fixture
def requirement():
    return RequirementSnapshot(
        id="req-1",
        title="Title",
        description="Description",
        criteria=(
            CriterionSnapshot(id="c1", text="first"),
            CriterionSnapshot(id="c2", text="second"),
        ),
    )


async def test_prepare_does_not_touch_network(requirement):
    engine = StubEngine()
    session = await engine.prepare(target=None, credentials=None)
    await session.close()


async def test_execute_returns_one_verdict_per_criterion_citing_real_evidence(requirement):
    sink = _FakeSink()
    engine = StubEngine()
    outcome = await engine.execute(session=None, requirement=requirement, sink=sink)

    assert len(outcome.criterion_verdicts) == len(requirement.criteria)
    assert len(sink.issued) == 1
    for verdict in outcome.criterion_verdicts:
        for evidence_id in verdict.cited_evidence_ids:
            assert evidence_id in sink.issued
