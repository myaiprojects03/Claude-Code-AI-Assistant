"""The citation-accuracy checker must itself be right: catch invented and mis-cited facts,
without flagging honest paraphrases."""

from assistant.grounding import check, facts_in

RUNBOOK = (
    "Each batch cuts over on a Friday at 18:00 after the partners' weekly meeting. "
    "Exchange 2016 stays as a hybrid server until batch three."
)
PROPOSAL = (
    "Move Northbridge Legal's 180 mailboxes to Microsoft 365. Matter sites use the "
    "scheme NBL-YYYY-####. Fixed price of £38,400 across three phases."
)
NETWORK = "Hub 10.40.0.0/22 in UK South. FortiGate 600F active-passive cluster."


def test_extracts_the_right_kinds_of_facts():
    f = facts_in(
        "1. Batch [2] at 6pm, £38,400, 180 users, NBL-YYYY-####, 10.40.0.0/22, 8%, "
        "600F, Exchange 2016, 3 phases"
    )
    assert {"18:00", "£38400", "180", "nbl-yyyy-####", "10.40.0.0/22", "8%", "600f", "2016"} <= f
    assert "2" not in f and "1" not in f and "3" not in f  # citations, list numbers, digits


def test_supported_answer_passes_including_paraphrased_times():
    g = check(
        "Batches cut over on Fridays at 6pm [1]. The fixed price is £38,400 [2].",
        [RUNBOOK, PROPOSAL],
    )
    assert g.ok and g.checked == 2


def test_invented_number_is_caught():
    g = check("They are moving 190 mailboxes [2].", [RUNBOOK, PROPOSAL])
    assert not g.ok and g.unsupported[0].startswith("190")


def test_partial_number_match_is_not_support():
    # "18" must not count as supported by "180" (or by "18:00").
    assert not check("Only 18 mailboxes are left.", [PROPOSAL]).ok


def test_wrong_citation_is_caught_even_if_the_fact_exists_elsewhere():
    g = check("The fixed price is £38,400 [1].", [RUNBOOK, PROPOSAL])  # it's in [2]
    assert not g.ok


def test_dangling_citation_is_caught():
    g = check("See the runbook [7].", [RUNBOOK])
    assert g.bad_citations == [7]


def test_uncited_fact_must_be_in_some_source():
    assert check("The hub range is 10.40.0.0/22.", [RUNBOOK, NETWORK]).ok
    assert not check("The hub range is 10.50.0.0/22.", [RUNBOOK, NETWORK]).ok


def test_facts_from_the_question_are_allowed():
    q = "Draft a follow-up after the 21 October meeting"
    assert check("Following our meeting on 21 October, ...", [RUNBOOK], question=q).ok
    assert not check("Following our meeting on 22 October, ...", [RUNBOOK], question=q).ok


def test_answer_with_no_specific_facts_has_nothing_to_check():
    g = check("I can't find that in the documents you have access to.", [RUNBOOK])
    assert g.ok and g.checked == 0


def test_redaction_replaces_only_invented_facts():
    from assistant.grounding import redact_unsupported

    given = RUNBOOK + " " + PROPOSAL
    text = (
        "1. Commercials: fixed price £38,400 [2], payment 50% on kickoff.\n"
        "2. Cutover Fridays at 6pm [1], over 36 weeks."
    )
    out, removed = redact_unsupported(text, given)
    assert removed == ["50%", "36"]
    assert "£38,400 [2]" in out and "6pm [1]" in out and "1. Commercials" in out
    assert "payment [TO CONFIRM] on kickoff" in out and "over [TO CONFIRM] weeks" in out


def test_redaction_keeps_facts_the_person_typed():
    from assistant.grounding import redact_unsupported

    out, removed = redact_unsupported("Budget is £20,000.", RUNBOOK, "our budget is £20,000")
    assert removed == [] and out == "Budget is £20,000."
