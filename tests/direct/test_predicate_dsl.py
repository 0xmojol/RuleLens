"""Deterministic layer: the closed predicate grammar.

These tests never touch an LLM. They exist to prove the claim in the README:
no generated code is executed, and an off-grammar predicate is reported as
invalid instead of being "best-effort" evaluated.
"""

import json

import pytest


TEXT = "We shipped v2 today. Docs: https://example.org/docs — thanks @team #release"
LONG_TEXT = "word " * 60


def explain(contract, predicate, text=TEXT):
    return json.loads(contract.explain_predicate(predicate, text))


@pytest.mark.parametrize(
    "predicate,expected",
    [
        ('has("shipped")', True),
        ('has("SHIPPED")', True),  # case-insensitive on purpose
        ('has("moon landing")', False),
        ('missing("moon landing")', True),
        ('missing("shipped")', False),
        ('count("v2") == 1', True),
        ('count("v2") >= 2', False),
        ("chars <= 200", True),
        ("chars > 10", True),
        ("links >= 1", True),
        ("links == 0", False),
        ("mentions >= 1", True),
        ("mentions <= 3", True),
        ("hashtags == 1", True),
        ("hashtags <= 0", False),
        ("words >= 5", True),
        ('has("shipped") and links >= 1', True),
        ('has("shipped") and links == 0', False),
        ('chars <= 500 and words >= 5 and has("@team")', True),
    ],
)
def test_valid_predicates_evaluate(direct_deploy, predicate, expected):
    contract = direct_deploy("contracts/rule_lens.py")
    result = explain(contract, predicate)
    assert result["valid"] is True, result
    assert result["result"] is expected, result


@pytest.mark.parametrize(
    "predicate",
    [
        "'shipped' in text",          # python expression: must never be executed
        "eval('1+1')",                # explicit eval attempt
        "__import__('os')",           # import attempt
        "has(shipped)",               # unquoted argument
        "",
        "chars",                      # measure without comparator
        "chars <=",                   # comparator without bound
        "chars <= -1",                # negative bound
        "has(" ,                      # unterminated
        'has("x"',                    # missing closing paren
        'has("")',                    # empty literal
        'unknown_measure("x")',       # measure outside the grammar
        'has("a") or has("b")',       # 'or' is outside the grammar
        'has("a") and',               # dangling conjunction
    ],
)
def test_off_grammar_predicates_are_invalid(direct_deploy, predicate):
    contract = direct_deploy("contracts/rule_lens.py")
    result = explain(contract, predicate)
    assert result["valid"] is False, result
    assert result["result"] is None
    assert result["error"]


def test_quoted_literal_may_contain_the_word_and(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    result = explain(contract, 'has("terms and conditions")', "see the terms and conditions page")
    assert result == {"valid": True, "result": True, "error": ""}


def test_escaped_quote_inside_literal(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    result = explain(contract, 'has("a\\"b")', 'the token a"b appears')
    assert result["valid"] is True
    assert result["result"] is True


def test_whitespace_is_normalized_before_measuring(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    assert explain(contract, "words == 3", "one   two\n\nthree")["result"] is True
    assert explain(contract, "chars == 13", "one   two\n\nthree")["result"] is True


def test_count_is_non_overlapping_and_case_insensitive(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    assert explain(contract, 'count("aa") == 2', "aaaa")["result"] is True
    assert explain(contract, 'count("go") == 3', "Go go GO")["result"] is True


def test_tag_counting_requires_a_tag_character(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    assert explain(contract, "mentions == 1", "mail me at user@example.org")["result"] is True
    assert explain(contract, "mentions == 0", "no handle here")["result"] is True
    assert explain(contract, "hashtags == 2", "#one and #two")["result"] is True


def test_long_text_measure_is_deterministic(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    assert explain(contract, "words == 60", LONG_TEXT)["result"] is True
    assert explain(contract, "chars > 250", LONG_TEXT)["result"] is True


def test_predicate_over_length_is_rejected(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    predicate = 'has("' + ("x" * 90) + '")'
    assert explain(contract, predicate)["valid"] is False


def test_clause_count_is_bounded(direct_deploy):
    contract = direct_deploy("contracts/rule_lens.py")
    predicate = " and ".join(['has("x")'] * 9)
    assert explain(contract, predicate)["valid"] is False
