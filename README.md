# RuleLens

**A GenLayer Intelligent Contract primitive: natural-language policy rules are compiled into a
closed predicate grammar, the verdict is computed in code, and every validator re-executes the
leader's predicates before agreeing.**

Most GenLayer contracts ask a model for a verdict and then check the shape of the answer. RuleLens
asks the model for something narrower — *which parts of this policy can be checked by machine* —
then decides the rest itself.

```
register_policy("launch-post", "Launch post policy", [
  "Post must include a link to the project",
  "Post must not exceed 280 characters",
  "Tone must be respectful and factual"
])
compile_policy("launch-post")
  -> coverage_pct 66, predicates ["links >= 1", "chars <= 280", ""]

check_submission("launch-post", "sub-1", text)
  -> verdict PASSED | REJECTED | NEEDS_REVIEW
```

## Why this is a primitive and not a wrapper

| Concern | RuleLens |
| --- | --- |
| A model writes the verdict | No. `_derive_verdict()` is the only place a verdict is produced. |
| A model writes code that gets executed | No. No `eval`, no `import`, no regex. A predicate either parses in the closed grammar below, or the rule is reported `NOT_MACHINE_CHECKABLE`. |
| A leader can fabricate a per-rule result | No. Each validator re-parses and re-executes the leader's own predicates on the same text. |
| A leader can smuggle a weak predicate | Audited: for every compiled predicate the validator runs its own `AUDIT_PREDICATES` judgment that the predicate fully enforces its rule. |
| Malformed model output becomes a pass | No. It degrades to `NEEDS_REVIEW`, never to `PASSED`. |
| The result is readable by another contract | Yes. `get_check()` / `get_policy()` are free views. |

## The closed predicate grammar

```
predicate := clause (" and " clause)*
clause    := has("text") | missing("text") | count("text") cmp N
           | chars cmp N | words cmp N | links cmp N | mentions cmp N | hashtags cmp N
cmp       := <= | < | >= | > | ==
"text"    := 1-80 characters, no newline, \" and \\ may be escaped
```

Semantics, all deterministic:

| Measure | Meaning |
| --- | --- |
| `has` / `missing` / `count` | case-insensitive, non-overlapping substring occurrences in the whitespace-normalized text |
| `chars` / `words` | length / token count of the whitespace-normalized text |
| `links` | occurrences of `http://` or `https://` |
| `mentions` | occurrences of `@` followed by at least one `[A-Za-z0-9_.-]` |
| `hashtags` | occurrences of `#` followed by at least one `[A-Za-z0-9_.-]` |

Anything else — `'x' in text`, `eval(...)`, `has(a) or has(b)`, a 9-clause conjunction — is
rejected by the parser and reported as invalid. `explain_predicate(predicate, text)` lets builders
test a predicate without touching consensus.

## What the model does, and what the code does

| Step | Done by |
| --- | --- |
| Validate inputs, sizes, duplicates, policy digest | code |
| Propose one predicate (or null) per rule index | model |
| Reject an off-grammar predicate and demote that rule to human judgment | code |
| Agree on the **checkable vector** (which rule indices have a predicate) | validators |
| Audit that each predicate fully enforces its rule | each validator, its own model |
| Evaluate every compiled predicate on the submitted text | code, on the leader and again on each validator |
| Judge only the rules no predicate covers | model |
| Derive `PASSED` / `REJECTED` / `NEEDS_REVIEW` | code |
| Store the record, coverage, and policy digest | code |

## Consensus design

Compilation (`compile_policy`):

| Field | Must match across validators | May differ |
| --- | --- | --- |
| checkable vector (per rule index: predicate or null) | **yes, exactly** | — |
| predicate text | no (each validator writes its own) | yes |
| faithfulness audit verdict | **yes, per predicate: must be true** | — |

Checking (`check_submission`):

| Field | Must match | May differ |
| --- | --- | --- |
| result of every `MACHINE` rule | **yes, byte-identical recomputation** | — |
| derived verdict | **yes** | — |
| judgment label for `JUDGMENT` rules | compared through the derived verdict | wording, reasoning |
| stored analysis / predicate text | not compared | yes |

Two properties worth stating plainly:

1. **If every rule compiles, agreement is fully deterministic.** No LLM call happens on the
   validator at all, so a disagreement is impossible unless a node is faulty.
2. **A missing or malformed judgment defaults to `UNCLEAR`**, which can only move the verdict
   toward `NEEDS_REVIEW`. Silence is never treated as compliance.

If validators disagree, the network rotates the leader and retries; if no funded rotation remains the
transaction is `Undetermined` and **no state is written**. `Accepted` is not `Finalized`; consumers
that need irreversible settlement must wait for finality.

## State design

All state is bounded; nothing grows per call except one record per distinct `submission_id`.

```
policies:  TreeMap[str, str]   # policy_id  -> canonical JSON (rules, predicates, coverage, digest, status)
checks:    TreeMap[str, str]   # submission_id -> canonical JSON verdict record
check_seq: u256                # monotonic counter exposed by get_counts()
```

Policy lifecycle: `REGISTERED` → `COMPILED` (one way). A changed policy is a new `policy_id`;
`policy_digest` is the SHA-256 of the normalized rule list so a consumer can bind a verdict to the
exact text that was compiled.

## API

| Method | Type | Purpose |
| --- | --- | --- |
| `register_policy(policy_id, title, rules_json)` | write | 1-12 rules, 3-240 characters each, no duplicates |
| `compile_policy(policy_id)` | write | consensus: build the checkable vector, audit, store coverage |
| `check_submission(policy_id, submission_id, text)` | write | consensus: evaluate + judge, derive and store the verdict |
| `get_policy(policy_id)` | view | full compiled policy |
| `get_check(submission_id)` | view | full verdict record |
| `get_counts()` | view | number of checks |
| `explain_predicate(predicate, text)` | view | parse and evaluate a predicate locally, no consensus |

`get_check()` is the integration point: another contract reads it, checks `policy_digest` and
`verdict`, and acts — without paying for consensus twice.

## Verification

```bash
pip install -r requirements.txt
genvm-lint check contracts/rule_lens.py
pytest tests -q
```

Current status on this machine (2026-11-21): `genvm-lint` → lint + validation passed, 7 methods;
`pytest tests/direct` → **64 passed**; deploy on a local 5-validator network (GLSim) →
`FINALIZED`, execution `SUCCESS`, 5/5 validators `agree`. The GLSim address is local-only and is not
evidence; the Studionet run below is. Coverage of the two layers:

* `tests/direct/test_predicate_dsl.py` — 46 cases over the grammar, including every rejection path
  (`eval`, python expressions, unquoted arguments, negative bounds, empty literals, 9 clauses).
* `tests/direct/test_rule_lens.py` — 18 cases over registration, compilation, checking, and
  validator behaviour, including:
  * a leader that flips a judgment is rejected (`test_validator_rejects_a_leader_that_flips_a_judgment`)
  * an unfaithful predicate blocks compilation (`test_unfaithful_predicate_blocks_compilation`)
  * model-written python is never accepted (`test_generated_python_is_never_accepted_as_a_predicate`)
  * prompt injection in the submission cannot change a machine rule
  * unusable model output fails closed to review, not to pass

### Live deployment evidence (Studionet, 2026-11-21)

Deployed from GenLayer Studio. Contract address:
`0x379Fb586b609A113B75a48e00Ea400038AB54cB7`
(<https://explorer-studio.genlayer.com/address/0x379Fb586b609A113B75a48e00Ea400038AB54cB7>).
All six transactions below reached `FINALIZED` with `result_name = MAJORITY_AGREE`.

| step | transaction |
| --- | --- |
| deploy | `0x7070620e5092f1d3fd6b3f218bd266c4ab75a5b60ade6181af416c5c0e211e45` |
| `register_policy("launch-post", …)` | `0x0d8600695ba6fcc1b530203b602f53690bd4ec9c2f8ae77f0c5055db303ac266` |
| `compile_policy("launch-post")` | `0x8a3033740c47aee9880d621e101b3649f74e291425710dfe91e940dec99529f3` |
| `check_submission("sub-pass")` | `0x6cc20b98a3735970881793bc9c915c18fb7af1e766f376c7b6ff5f3a73498701` |
| `check_submission("sub-reject")` | `0x2f950e8463863472d85dba6da8865bfcac43ac443699a3b01baa8306abfc276c` |
| `check_submission("sub-inject")` | `0xca05c5d9150be86807200b244939af60602d1b1dea22d8ff99b26ae935bf5338` |

Read back from the contract itself (not from Studio's log):

```
get_policy("launch-post") -> status COMPILED, coverage_pct 66,
                             predicates ["links >= 1", "chars <= 280", ""],
                             policy_digest 7df3fd7f3522c289f235febf336abdc3bce84ac047c1acd6b9a06c49504e410c
get_counts()              -> {"checks": 3}
get_check("sub-pass")     -> check_id 1, PASSED   SATISFIED / SATISFIED / SATISFIED
get_check("sub-reject")   -> check_id 2, REJECTED VIOLATED / SATISFIED / SATISFIED
get_check("sub-inject")   -> check_id 3, REJECTED VIOLATED / SATISFIED / VIOLATED
```

What this run establishes:

* The verdict is produced by a live multi-validator network, not by a local mock. Each write call
  committed, revealed, and settled with three `agree` votes (the remaining validators are cancelled
  once quorum is reached, which is why they read `idle`).
* `sub-inject` contains no link and instructs the model to answer `PASSED`. The link rule is
  computed in code, so the verdict is `REJECTED` whatever the model says: an injected instruction
  cannot buy a passing verdict. In this run the model also judged the tone rule `VIOLATED`, but the
  verdict does not depend on that.
* The two rotations (`compile_policy`, `sub-pass`) are the validator layer doing its job. On the
  first compile round the leader produced a weaker predicate set (`coverage_pct 33`, the link rule
  left as judgment) and the validators refused it; the network rotated and the next round produced
  the 66% set. The network settles on the predicate vector, because that vector *is* the semantics.
* The deployed source is identical to `contracts/rule_lens.py` — 740 lines, compared line by line
  against the `contract_code` in the deploy transaction; the only difference is CRLF line endings.

### Toolchain note (reproducing these tests)

`genlayer-test 0.29.2` tries to download `genvm-universal.tar.xz` from the newest genvm release.
That asset stopped shipping with `v0.3.0-rc0`, so direct-mode tests abort with `HTTP 404: Not Found`
before any contract code runs. Pre-seed the cache with the last release that ships it:

```bash
mkdir -p ~/.cache/gltest-direct
curl -L -o ~/.cache/gltest-direct/genvm-universal-v0.2.16.tar.xz \
  https://github.com/genlayerlabs/genvm/releases/download/v0.2.16/genvm-universal.tar.xz
```

Also: the header pins `py-genlayer:1jb45aa8...` (the runner present in that artifact). The linter
reports a newer runner (`1zr6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh`); if you deploy from
Studio, use the header Studio's own examples use and re-run the tests.

### Toolchain note: GLSim deploys a contract once per process

GLSim caches the compiled contract by content hash inside the running process. Measured on
2026-11-21 with the identical command run twice against one `glsim --validators 5` process:

```
run 1  STATUS: FINALIZED | EXECUTION: SUCCESS
run 2  STATUS: FINALIZED | EXECUTION: ERROR
       ('class is not marked for usage within storage, please, annotate it with @allow_storage',
        <class '_contract_glsim_contract_...RuleLens'>)
```

The second deploy of the same bytes fails; the first one after a restart succeeds. So: restart
`glsim` between repeated deploys of the same contract, and do not read that message as a defect in
the contract. (The same test was also run with a `-> dict` view and with a renamed class to rule
both out; `-> dict` deploys fine.)

## Deploying in GenLayer Studio

1. Open <https://studio.genlayer.com>, Contracts tab, create a file `rule_lens.py` and paste
   `contracts/rule_lens.py` **including the first comment line**.
2. Deploy with no constructor arguments.
3. Write calls, in order:
   * `register_policy` with `policy_id = "launch-post"`, `title = "Launch post policy"`,
     `rules_json` = the JSON array in `fixtures/rules_launch.json`.
   * `compile_policy` with `policy_id = "launch-post"` — read `coverage_pct` from the receipt.
   * `check_submission` three times, with `fixtures/submission_pass.txt`
     (expect `PASSED`), `fixtures/submission_reject.txt` (expect `REJECTED`, no link),
     `fixtures/submission_injection.txt` (no link, and an instruction telling the model to answer
     `PASSED`; expect `REJECTED` — the link rule is decided deterministically whatever the injected
     instruction says, while the tone rule is a model judgment and may come back `SATISFIED`,
     `VIOLATED` or `UNCLEAR`).
4. Read `get_check("sub-pass")`, `get_check("sub-reject")`, `get_check("sub-inject")`.
5. Compare with the Studionet results recorded above; a fresh deployment will agree on every
   verdict, though judgment-mode rules and the number of consensus rounds may vary.

## Honest limitations

* The grammar is deliberately small. Rules that do not reduce to it stay `JUDGMENT`, and the
  coverage percentage tells you exactly how much of the policy is machine-enforced.
* The faithfulness audit is a model judgment, not a proof. It catches weak or off-target
  predicates; it cannot prove that no adversarial predicate exists.
* Compilation is strict: two honest models that disagree about whether a rule is machine-checkable
  will not settle. That is intentional — it is the signal that the rule is not mechanically
  enforceable — but it means ambiguous policies may need rewording.
* RuleLens records rule compliance. It is not a legal, regulatory, or financial certification.

## License

MIT.
