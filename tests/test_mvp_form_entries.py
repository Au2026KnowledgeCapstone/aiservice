from engines.mvp.form_entries import is_form_entry, matched_entry


def test_add_new_create_buttons_are_form_entries():
    assert is_form_entry({"role": "button", "name": "Add Patient", "has_form": False})
    assert is_form_entry({"role": "link", "name": "New Report", "has_form": False})
    assert not is_form_entry({"role": "button", "name": "Save", "has_form": False})


def test_submit_buttons_inside_a_form_are_never_entries():
    assert not is_form_entry({"role": "button", "name": "Add Patient", "has_form": True})


def test_dangerous_verbs_disqualify_even_matching_phrasing():
    assert not is_form_entry({"role": "button", "name": "Add and Delete Patient", "has_form": False})


def test_matched_entry_requires_exact_source_and_identity_match():
    control = {"locator": {"role": "button", "name": "Add Patient"}, "role": "button", "name": "Add Patient", "has_form": False}
    obs = {
        "url": "https://example.test/", "visual_content_hash": "abc",
        "form_entry_contracts": [{
            "source_url": "https://example.test/", "source_hash": "abc",
            "control": {"locator": {"role": "button", "name": "Add Patient"}},
            "destination_url": "https://example.test/new", "effect": "navigation",
        }],
    }
    entry = matched_entry(control, obs)
    assert entry["destination_url"] == "https://example.test/new"


def test_matched_entry_is_none_without_a_matching_transition():
    control = {"locator": {"role": "button", "name": "Add Patient"}, "role": "button", "name": "Add Patient", "has_form": False}
    obs = {"url": "https://example.test/", "visual_content_hash": "abc", "form_entry_contracts": []}
    assert matched_entry(control, obs) is None
