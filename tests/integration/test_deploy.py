"""Deploy-only integration check against a running GenLayer network (GLSim or Studio).

Run with a local network up:
    glsim --port 4000 --validators 5 &
    gltest tests/integration -v -s

Method calls are not asserted here: GLSim 0.29.2 answers
`gen_get_contract_schema_for_code` with an empty `methods` map, so the dynamic
`contract.<method>` wrapper cannot be built. Studio does not have that gap, which is
where the write flow is exercised.
"""

import json

import pytest

from gltest import get_contract_factory


def test_deploy_is_accepted_with_validator_agreement(default_account):
    try:
        factory = get_contract_factory("RuleLens")
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"contract loading unavailable: {exc}")

    receipt = factory.deploy_contract_tx(args=[])

    address = receipt["data"]["contract_address"]
    votes = receipt.get("consensus_data", {}).get("votes", {})
    leader = receipt.get("consensus_data", {}).get("leader_receipt", [])
    execution = leader[0].get("execution_result") if leader else None

    stderr = ((leader[0].get("genvm_result", {}) or {}).get("stderr", "") if leader else "")
    print("STATUS:", receipt.get("status_name"), "| EXECUTION:", execution)
    print("STDERR:", stderr[:1200])
    print("ADDRESS:", address)
    print("VOTES:", json.dumps(votes))

    assert receipt.get("status_name") == "FINALIZED"
    assert execution == "SUCCESS"
    assert votes and all(vote == "agree" for vote in votes.values())
    assert address.startswith("0x") and len(address) == 42
