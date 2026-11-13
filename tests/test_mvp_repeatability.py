"""Step 6 checkpoint (repeatability.py): the fingerprint is computed, never persisted."""
from engines.base import Target
from engines.mvp.contracts import EngineConfiguration
from engines.mvp.repeatability import fingerprint


def test_fingerprint_is_deterministic_for_the_same_inputs():
    requirement = {"id": "r1", "title": "x", "description": "y", "acceptance_criteria": ["z"]}
    target = Target("https://example.test/", 1280, 800)
    configuration = EngineConfiguration()
    first = fingerprint(requirement, target, configuration)
    second = fingerprint(requirement, target, configuration)
    assert first == second
    assert first["viewport"] == [1280, 800]
    assert first["target_url"] == "https://example.test/"


def test_fingerprint_changes_with_the_requirement():
    target = Target("https://example.test/", 1280, 800)
    configuration = EngineConfiguration()
    a = fingerprint({"id": "r1", "title": "x", "description": "y", "acceptance_criteria": ["z"]}, target, configuration)
    b = fingerprint({"id": "r1", "title": "x", "description": "different", "acceptance_criteria": ["z"]}, target, configuration)
    assert a["requirement_fingerprint"] != b["requirement_fingerprint"]


def test_fingerprint_is_a_plain_dict_with_no_persistence_side_effect():
    # The point of this test is documentary: fingerprint() takes no database
    # session and has no caller obligation to store its result anywhere.
    import inspect
    from engines.mvp import repeatability

    signature = inspect.signature(repeatability.fingerprint)
    assert "db" not in signature.parameters
    assert "sessions" not in signature.parameters
