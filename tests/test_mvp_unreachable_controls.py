"""Reporting the controls the engine could see but could not use.

Always on: pure functions over observation dicts, no browser and no AI spend.
The fixtures mirror the real Amazon-Clone header, where three icon-only links
carry no accessible name.
"""
from engines.mvp.diagnostics import unreachable_controls, unreachable_summary


class Redactor:
    def text(self, value, limit):
        return str(value)[:limit]


def control(**overrides):
    base = {"element_id": "o1-e0", "element": "a", "role": "link", "name": "Open",
            "actionable": True, "locator": {"role": "link", "name": "Open"}, "href": None}
    return {**base, **overrides}


# The real header: an icon-only <a> has no text, no aria-label and no title, so
# inspect() derives no name, computes no locator and withdraws the control.
UNNAMED_CART = control(element_id="o1-e1", name="", locator=None, actionable=False,
                       href="https://shop.test/cart")
UNNAMED_SIGNIN = control(element_id="o1-e2", name="", locator=None, actionable=False,
                         href="https://shop.test/signin")


def observation(*controls):
    return {"observation_id": "o1", "url": "https://shop.test/", "controls": list(controls)}


def test_an_unnamed_control_is_reported_with_its_destination():
    found = unreachable_controls([observation(UNNAMED_CART, control())], Redactor())

    assert len(found) == 1
    assert found[0]["reason"] == "no accessible name"
    assert found[0]["endpoint"] == "https://shop.test/cart"


def test_a_usable_control_is_never_reported():
    assert unreachable_controls([observation(control())], Redactor()) == []


def test_a_disabled_control_is_not_a_finding():
    """Disabling a control is usually deliberate; it does not describe a defect."""
    disabled = control(disabled=True, actionable=False)

    assert unreachable_controls([observation(disabled)], Redactor()) == []


def test_a_redundant_copy_is_not_a_finding():
    """Discovery withdraws duplicate copies of one interchangeable control on
    purpose, so reporting them would be noise rather than a cause."""
    copy = control(actionable=False, redundant_copy=True, href="https://shop.test/p1")

    assert unreachable_controls([observation(copy)], Redactor()) == []


def test_ambiguity_is_collected_but_not_narrated():
    """Several controls sharing one identity explains an abstention, but it
    describes a layout rather than something to go and fix."""
    ambiguous = control(name="Mouse", actionable=False, ambiguous=True)

    found = unreachable_controls([observation(ambiguous)], Redactor())

    assert [item["reason"] for item in found] == ["several controls share one identity"]
    assert unreachable_summary(found) is None


def test_the_same_control_across_observations_is_counted_once():
    found = unreachable_controls(
        [observation(UNNAMED_CART), observation(UNNAMED_CART), observation(UNNAMED_CART)], Redactor()
    )

    assert len(found) == 1
    assert found[0]["occurrences"] == 3


def test_the_summary_names_what_could_not_be_tested():
    found = unreachable_controls([observation(UNNAMED_CART, UNNAMED_SIGNIN)], Redactor())

    summary = unreachable_summary(found)

    assert "2 visible controls have no accessible name" in summary
    assert "https://shop.test/cart" in summary
    assert "https://shop.test/signin" in summary


def test_the_summary_is_singular_for_one_control():
    summary = unreachable_summary(unreachable_controls([observation(UNNAMED_CART)], Redactor()))

    assert summary.startswith("1 visible control has no accessible name")


def test_control_names_are_redacted():
    """Names are page content and must not reach a user-facing message raw."""
    class Truncating:
        def text(self, value, limit):
            return "[redacted]"

    found = unreachable_controls([observation(control(name="Mouse", actionable=False, ambiguous=True))], Truncating())

    assert found[0]["name"] == "[redacted]"
