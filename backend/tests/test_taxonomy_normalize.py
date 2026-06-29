"""F5 — normalize / title-resolution edge cases.

The seniority-stripping in ``normalize_surface`` used to over-strip the
manager/lead track: 'Tech Lead' -> 'tech', 'Head of Engineering' -> 'engineering',
'technical lead manager' -> 'technical'. Those mangled forms either dead-ended the
resolver or made an eng-mgr alias collide with any plain engineering title. The fix
makes seniority-stripping non-destructive (keeps role-bearing 'lead'/'head of'/
'manager' tokens) and falls back to the un-stripped form when stripping would empty
the string. This module pins that behavior so it can't silently regress.
"""
from __future__ import annotations

import backend.warehouse.taxonomy as tx
from backend.warehouse.taxonomy import match_title_to_role, normalize_surface


def _reset_index():
    # the title index is memoized off the curated seed; clear it so reloads are honest
    tx._TITLE_INDEX = None


def test_ic_seniority_still_strips():
    # pure IC-seniority modifiers must still collapse to the bare role noun
    assert normalize_surface("Sr. Software Engineer II") == "software engineer"
    assert normalize_surface("Senior Data Engineer") == "data engineer"
    assert normalize_surface("Staff Data Engineer") == "data engineer"
    assert normalize_surface("Junior Frontend Engineer") == "frontend engineer"


def test_lead_and_head_are_not_over_stripped():
    # role-bearing manager/lead tokens survive — no mangling to a bare modifier
    assert normalize_surface("Tech Lead") == "tech lead"
    assert normalize_surface("Head of Engineering") == "head of engineering"
    assert normalize_surface("Engineering Lead") == "engineering lead"
    assert normalize_surface("technical lead manager") == "technical lead manager"
    assert normalize_surface("Director of Engineering") == "director of engineering"


def test_seniority_only_surface_is_non_destructive():
    # stripping a seniority-only surface would empty it — keep the words verbatim
    assert normalize_surface("Staff") == "staff"
    assert normalize_surface("Senior") == "senior"


def test_skill_punctuation_survives():
    # +,#,.,/ are kept so stack tokens are not destroyed
    assert normalize_surface("C++ Engineer") == "c++ engineer"
    assert normalize_surface("CI/CD Engineer") == "ci/cd engineer"


def test_empty_and_none_safe():
    assert normalize_surface("") == ""
    assert normalize_surface(None) == ""


def test_manager_track_titles_resolve_to_eng_mgr():
    _reset_index()
    for t in ("Head of Engineering", "Director of Engineering",
              "VP of Engineering", "engineering lead", "technical lead manager"):
        assert match_title_to_role(t) == "eng-mgr", t


def test_ic_titles_resolve_correctly():
    _reset_index()
    assert match_title_to_role("Senior Software Engineer II") == "swe"
    assert match_title_to_role("Staff Data Engineer") == "data-eng"
    assert match_title_to_role("Lead Data Scientist") == "data-sci"
