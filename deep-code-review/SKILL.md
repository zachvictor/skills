---
name: deep-code-review
description: >
  Use this skill for any code review task — reviewing a diff or branch, drafting inline
  review comments, generating MR/PR titles and descriptions, writing commit messages, or
  reasoning about whether a change is correct and complete. Trigger on phrases like "review
  this", "look at this diff", "what do you think of this change", "draft MR description",
  "write a review comment", "is this ready to merge", "check this branch against develop",
  or any context where the user is working through a merge request, pull request, or
  changelist. Also trigger when the user asks you to act as a reviewer, pair programmer, or
  second pair of eyes on code. Even short tasks ("just write a comment") benefit from the
  review guidance here — do NOT skip the skill for those.
---

# Code Review Skill

You are a senior engineer helping with code review. You operate in three modes:

1. **Reviewer mode** — reading someone else's diff, identifying issues, drafting comments,
   reasoning about correctness, naming, architecture, security, and test coverage.
2. **Self-Review mode** — reading the user's own diff before they post it. Different
   discipline from reviewing a stranger's code; covered in Part 3.
3. **Author mode** — generating MR/PR titles, descriptions, and commit messages on behalf
   of the person who wrote the code.

If the repo has an agent instructions file (e.g., `AGENTS.md` or equivalent),
check it first — it may refine or override conventions here.
If it doesn't (most won't), apply the guidance below directly.

---

## Part 1: How to Read a Diff

Reading a diff well is a skill in itself. Don't just scan top-to-bottom. Use this approach:

### 1. Orient from the outside in
Start with the MR description and commit messages (if available) to understand stated intent.
Then look at the file list to gauge scope — how many files, which layers, any surprises
(config changes? migrations? new dependencies?).

### 2. Read tests first
Tests reveal intent more reliably than implementation. Read new or modified tests before the
production code they exercise. Ask: what behavior is being added or changed? Are the test
cases covering edge cases or just the happy path?

### 3. Read structural changes before logic changes
Schema changes, new files, interface definitions, and dependency additions are higher-leverage
review targets than individual function bodies. Get the shape right first.

### 4. Trace data flow across files
A change that touches a handler, a service layer, and a data model is really one logical
change spanning three files. Review it as a unit. Check that changes in one layer are
reflected in the others — a new field in a struct that never gets populated, a new endpoint
with no corresponding test, a migration with no rollback.

### 5. Look for what's missing
The hardest review skill. After reading what changed, ask what *should* have changed but
didn't: missing error handling, missing validation, missing tests, missing documentation,
missing migration, missing config for a new feature.

---

## Part 2: Review Philosophy

These principles apply regardless of language, platform, or project.

### Calibrate severity honestly
Every comment should carry a severity. Use this vocabulary consistently:

| Label | Meaning |
|---|---|
| `nit:` | Style or polish. Take it or leave it. |
| `concern:` | A real concern worth addressing, but not a blocker. Often left unlabeled in practice — the unmarked default tier — but `concern:` is the explicit form. |
| `blocking:` | Must be resolved before merge. |
| `question:` | You need more context before you can judge. |
| `suggestion:` | An alternative worth considering, not a mandate. |

Do not inflate severity. An inconsistent variable name is a nit. A missing auth check is
blocking. If you're unsure, default to `question:` — it's always fair to ask.

### Distinguish new problems from inherited ones
Before flagging something, determine whether this MR introduced it or it already exists.
If it pre-exists: note it briefly, attribute it correctly ("this pattern exists elsewhere in
the codebase — worth a follow-up ticket"), and do not block the current MR on it.

### Distinguish the author's error from the codebase's error
If a contributor copied an existing antipattern faithfully, the review should acknowledge
that. Tone matters: "You're following an established pattern here. It's worth flagging to the
team, but not something to block on you fixing alone."

### Ask before blocking on ambiguity
When a concern depends on context you don't have (is this endpoint public or internal-only?
is this data sensitive?), frame it as a question that must be answered before merge — not a
verdict.

### Keep scope to what the MR changes
Do not demand fixes to pre-existing code not touched by the MR. Do not conflate "things that
could be better" with "things the author broke." Do not use a review as an opportunity to
redesign the feature. You can note out-of-scope concerns briefly and suggest a follow-up
ticket.

### Flag oversized MRs
If an MR is large enough that thorough review is impractical (many files, mixed concerns,
multiple logical changes bundled together), say so. A large MR isn't automatically wrong, but
the reviewer should note the risk: "This is a large change — I've reviewed it carefully, but
the scope makes it harder to catch subtle issues. Consider splitting in future."

### Present findings, don't render a verdict
Do not approve or reject the MR. Present your assessment — concerns, nits, questions, and
what looks good — and let the human decide. If you found nothing to flag, say so plainly:
"I reviewed the diff and found no concerns." That is the strongest positive signal, and it
requires the human to read it and own the decision.

A review is not a search for perfection. But if the same nit recurs many times, that's a
pattern — escalate it to a `concern:`, not "just nits."

Don't be the reviewer who always finds one more thing. If the code is solid, say so.

---

## Part 3: Self-Review Mode

Reviewer mode (above) and Author mode (below) cover other people's diffs and the metadata
you publish around your own. Self-Review Mode is a third stance: reading your own diff
before posting it. The discipline is different from reviewing a stranger's code — you
know what you intended, and that knowledge is the problem.

### The mental shift: read for what got dropped, not what got added
The diff already shows what's new. Refactor MR bugs hide in what's *missing* from the new
version of a function relative to the old one — an argument the old caller passed and the
new one doesn't, a return value the old caller honored and the new one discards, a side
effect the old code ran and the new code skips.

For each modified function, list (out loud or on paper) what arguments, return values, and
side effects the old version had. Then verify each is preserved by the new version,
intentionally changed (with a test pinning the new behavior), or intentionally removed
(with a callsite sweep confirming nothing depended on it). "Tests still pass" does not
establish any of these.

### Distrust green CI and clean structure
Tests cover units in isolation. Refactor seams sit *between* units — places where one
component's output is another's input. Both can be individually green while the wire
between them is wrong. The sentence to memorize: *"tests pass and the structure looks
clean"* is the precondition for shipping a refactor, not the evidence that it works.

### Trace one real row
Pick one representative input — for an ingest pipeline, an org-scoped CDC file; for an
API, a real authenticated request — and walk it through the entire call graph in the new
code, with the old code open in another window. At every contract boundary (function call,
message send, write to a queue), confirm the new version preserves whatever the old
version did. This catches the things tests don't because the tests are pointed at the
components, not the contracts.

### Read the diff once forward, once backward
Forward read: did the new code do everything the old code did? Backward read: does the new
code reference anything the old code never produced? The first catches dropped behavior;
the second catches phantom references — variables that were renamed in one place but not
another, columns that the producer no longer adds, return values that the new contract no
longer guarantees.

### Internal contract testing
The lasting fix for the bugs Self-Review Mode is trying to catch is *internal contract
tests*: tests that pin the *wires between components*, not just the components themselves.

The pattern: stub a downstream collaborator with a capturing fake, run the upstream code
with realistic input, and assert on what the stub received. For example, if a pipeline
runner is supposed to forward `org_id` from a config dict into a reader's `read()` call,
the test passes a config dict carrying `org_id='900004'`, runs the pipeline runner with a
`_CapturingReader` whose `read()` records its arguments, and asserts the reader saw
`org_id='900004'`. The reader can return `None` to short-circuit the rest of the pipeline
— the test cares about the wire, not the body.

Two properties make this category of test especially load-bearing:

1. **It pins the seam, not the implementation.** The reader's body can change freely; the
   test only fails if the *contract* between runner and reader changes silently.
2. **It runs in unit-test time, not integration time.** No real Spark session, database, or
   network call. A capturing-mock test for the wire above runs in milliseconds; the
   equivalent end-to-end harness test takes minutes.

Every contract a refactor crosses deserves at least one such test. Concretely, before
merging a refactor, every function whose signature changed, every dict whose keys were
renamed, and every Protocol/interface whose method order shifted should have a test that
breaks if the *contract* regresses, not just if the implementation is wrong. The bug
classes catalogued in "Refactor seams" (Part 4) all become catchable once contract tests
exist at the relevant wire.

### What good looks like
- Self-Review Mode produces at least one concern you intend to fix before posting.
- If it produces zero concerns and the diff is non-trivial, the most likely explanation is
  that you read the diff for what was added, not what was dropped — re-read.
- A diff with `=None`, `=False`, or `= None` near a refactor seam deserves a second look.
  So does a `with` block that has a `return` inside it. So does a function whose only
  callers are tests.

### Worked examples
Two cases drawn from a recent refactor MR where Self-Review Mode would have caught the
bug at the seam. Both formatted in the structured findings format from Part 11.

#### F1 [blocking] Dropped argument: `org_id` not forwarded into ReadStage

**Where:** `bronze/pipeline/table_runner.py::run_table`, after a refactor that introduced
a `StageContext` dataclass.

**Why it matters:** `add_metadata_columns` builds `unique_key = f"{table}_{org_id}_{uuid}"`
when `org_id` is present and `f"{table}_{uuid}"` when it isn't, so dropping `org_id`
silently collapses `unique_key` across orgs that share a uuid. Tests on the upstream
parser (`parse_table_name`) and the downstream metadata helper were green; the wire
between them had no test.

**Evidence:** Upstream `group_tables_for_dispatch` correctly populated
`table_info['org_id']` for org-prefixed tables. Downstream `ReadStage` correctly threaded
`ctx.org_id` into `reader.read(...)`. The wire between them was severed by a hardcoded
`org_id=None` on the `StageContext` construction:

```python
ctx = StageContext(
    spark=spark,
    cfg=cfg,
    ...
    file_paths=file_paths,
    org_id=None,   # <-- hardcoded; ignores table_info['org_id']
)
```

**Suggested fix:**
```python
org_id = table_info.get('org_id')   # None for non-org tables
ctx = StageContext(..., org_id=org_id)
```

**Internal contract test that pins it:**
```python
def test_org_id_forwarded_to_reader():
    reader = _CapturingReader()
    table_info = {'org_id': '900004', ...}
    run_table(spark=None, cfg=cfg, table_name='org_900004_things',
              table_info=table_info, reader=reader, sink=_NoopSink())
    assert reader.captured_org_id == '900004'
```
The reader's `read` method captures kwargs and returns `None`, short-circuiting the rest
of the pipeline. No real Spark session needed.

**The grep that surfaces it:** `=None` or `=False` literals near a freshly introduced
dataclass or kwargs unpack, especially where the same dict carries the value (here,
`table_info['org_id']` is in the call frame).

#### F2 [blocking] Undefined variable in fallback path: `TEST_DIR` vs `TEST_DATA_DIR`

**Where:** `tests/integration/download_parquet_test_data.sh`, the broaden-search fallback
branch.

**Why it matters:** The fallback never triggers. Operators run the happy path
successfully and never know the recovery branch is broken — exactly the path they rely
on when the happy path produces nothing. The bug class is mechanically detectable; the
only reason it survived self-review is that nobody ran the fallback.

**Evidence:**
```bash
TEST_DATA_DIR="$(dirname "$0")/test_data_parquet"
...
if [ ! -d "${TEST_DIR}/datalakehouse-singlestore" ] || [ $(find "${TEST_DIR}/...
```
`TEST_DIR` is undefined; `TEST_DATA_DIR` is defined. The script had been touched in a
`gsutil` → `gcloud storage` sweep that didn't catch the variable-name drift. With
`set -e` but no `set -u`, the unbound expansion silently becomes the empty string and
the directory check passes — masking the bug instead of surfacing it.

**Suggested fix:** rename `TEST_DIR` → `TEST_DATA_DIR` (two occurrences). Consider adding
`set -u` at the top of the script to fail loudly on future undefined-variable typos.

**The grep that surfaces it:** for each `${VAR}` reference in shell or Python, confirm a
defining assignment is in the same scope. For shell, also audit whether `set -u` is
active — silent unbound expansion is the failure mode that hid this bug.

---

## Part 4: What to Look For

This is a prioritized checklist of review concerns, from highest to lowest impact. Not every
item applies to every MR — use judgment.

### Correctness
Does the code do what the MR description says it does? Are edge cases handled? Is the logic
sound, or is there an off-by-one, a nil dereference waiting to happen, a race condition?
Correctness issues are almost always blocking.

### Refactor seams
A class of correctness bug that recurs in refactor MRs and survives green CI. Each one is
mechanically detectable; none of them are caught by unit tests on the components in
isolation.

- **Dropped arguments at seams.** A caller used to pass `foo`; the new caller doesn't.
  Often surfaces as a hardcoded `foo=None` or `foo=False` literal next to a dict or object
  that still carries `foo`. Look for argument defaults introduced at the same seam where
  data is being routed through a dict or kwargs unpack. *(Default severity: blocking —
  silently changes runtime behavior.)*
- **Swallowed return values.** A function whose return type carries information (`bool` for
  success/failure, an enum for a state, an error object) being called as if it returned
  `None`. Grep boolean-returning helpers for call sites that discard the return — those
  are latent fail-silent paths. *(Default severity: blocking — fail-silent paths corrupt
  error locality.)*
- **Early returns inside lifecycle blocks.** A `return` from inside `with`, `try/finally`,
  or any context manager that owns exit handling. The lifecycle still runs (`__exit__`
  fires), but the return path may skip uniform exit-status accumulation, summary logging,
  or post-processing. Prefer one return point per lifecycle scope. *(Default severity:
  concern — usually a maintainability landmine, occasionally a correctness bug
  depending on what the lifecycle owns.)*
- **Functions with no callers.** After a refactor that splits a module, every function in
  the new layout should have at least one caller in production code. Grep the function
  name; if the only matches are the definition and its tests, the function is dead —
  delete it or wire it up. Don't leave it for later. *(Default severity: concern —
  not a runtime bug, but a misleading signal about what the new layout actually does.)*
- **Undefined variables in fallback paths.** Variable typos that the happy path never
  exercises, hiding behind `if not_found:` branches or `set -e` shell scripts. Grep both
  ways: every `${VAR}` should have a defining assignment in the same scope; every
  assignment should have at least one consumer. *(Default severity: blocking — the bug
  ships and only fires in recovery branches, where operators rely on it most.)*
- **Unsafe string concatenation into program text.** SQL filters, jq programs, shell
  commands, regex patterns — anywhere a user-provided string lands inside a *program*, not
  a *value*. Pass user input as data (parameter binding, jq `--arg`, shell arrays), never
  as identifier text in the program string. Test with adversarial inputs (dashes, dots,
  spaces, language keywords, empty strings) before assuming the happy path is safe.
  *(Default severity: blocking — security-or-correctness depending on the program
  language; at minimum a robustness gap.)*
- **Stale references to deleted infrastructure.** When a *prior* change removed a file,
  service, dependency, CLI, or environment, the next refactor in the same area inherits
  responsibility for grepping the repo for references to the removed thing — *before*
  declaring adjacent code "preserved." `set -e` failures and runtime errors hide stale
  references in code paths that aren't routinely exercised, so the rot is invisible until
  someone runs the code. A script that is never run looks live; only `grep` reveals it
  isn't. *(Default severity: blocking — code that looks live and is dead is worse than
  code that is obviously dead, because it misleads operators.)*
- **Doc/code drift after partial migration.** When the README announces *"X has been
  removed; we now use Y"* but the code still uses X, the migration is half-finished. The
  next person to follow the docs hits a broken script. Cross-check: every doc claim of
  removal must correspond to all code-level call sites being updated; every doc claim of
  *"now uses Y"* should correspond to every script/test/CI job that previously used X
  referencing Y. When self-reviewing a migration-flavored MR — yours or someone else's —
  search the repo for references to the old thing the docs say is gone. *(Default
  severity: blocking — the docs lie until the code catches up, and operators will trust
  the docs.)*

These bugs share a precondition: the unit tests for each component are green. Catching
them requires either (a) Self-Review Mode discipline (Part 3), (b) internal contract tests
(Part 3, "Internal contract testing"), or (c) an external reviewer who reads the diff for
what was dropped, not what was added.

### Security
- **Auth on new endpoints**: any new route needs explicit auth handling. If it's missing, ask
  whether the endpoint is internal-only or externally reachable. Externally reachable without
  auth is blocking.
- **Injection**: SQL built with string concatenation, unsanitized user input in templates or
  shell commands.
- **Secrets**: hardcoded credentials, API keys, tokens. Even in "dev-only" code.
- **SAST suppression**: do not silence static analysis (`#nosec`, `nolint`, etc.) without a
  comment explaining why.

### Concurrency and race conditions
- Shared mutable state accessed from multiple threads or coroutines without synchronization.
- Thread/task leaks: concurrent work launched without a shutdown path or cancellation.
- Deadlocks from improper lock ordering or blocking on unread channels/queues.
- Language-specific pitfalls are covered in the Go and Python sections below.

### API and contract changes
- Is this change backward-compatible? Will existing clients break?
- For protobuf: are field numbers stable? Are new fields additive (not renaming or removing)?
- For JSON APIs: are new fields optional with sensible defaults? Are removed fields actually
  unused by all consumers?
- For gRPC: does the service definition change in a way that requires coordinated deployment?

### Performance (when it matters)
Don't micro-optimize every review, but flag clear problems:
- O(n²) or worse in a hot path.
- N+1 queries (a loop that makes a DB or RPC call per iteration).
- Unbounded collection growth (appending to a collection in a loop with no size limit).
- Missing pagination on list endpoints.
- Large allocations in request-scoped code that could be pooled.

### Error handling
- Errors must not be silently discarded.
- Error messages should be specific enough to debug without the source code open.
- Wrapping should add context at each layer without losing the original error.

### Test coverage
- New behavior should have tests. If it doesn't, ask why.
- Tests should assert meaningful things — not just that the function didn't panic.
- Test failure messages should include actual and expected values.
- Flaky test patterns: time-dependent assertions, port collisions, ordering assumptions on
  maps or goroutines.

### Naming and clarity
Naming is worth commenting on when a name is actively misleading — not when you'd merely
prefer a synonym. "This function is called `Process` but it only validates" is a real
concern. "I'd call this `items` instead of `entries`" is a nit at best.

### Configuration and deployment
- Hardcoded values that should be configurable (URLs, timeouts, thresholds, resource names).
- Docker images pinned to `:latest` instead of a specific tag or digest.
- Helm/k8s changes: resource names, subscription names, and similar identifiers should be
  consistent across environments (regions, clusters). Verify overrides match.
- New environment variables or config keys documented somewhere (README, values schema, etc.).

### Logging
Flag these problems in any MR that touches logging:

- **Informationally deficient log lines.** A log message like `"error occurred"` or
  `log.Error("failed")` with no context — no operation name, no relevant IDs, no error
  value — is a review concern, not a nit. The person debugging this at 2 AM needs to know
  *what* failed, *for which input*, and *why*.
- **Unstructured logging.** Prefer structured logging (Go: `slog`, `zap`; Python: `structlog`,
  stdlib with `logging.config`; JS/TS: `pino`, `winston`) over string interpolation into
  print-style calls. Structured fields are searchable in observability tooling; interpolated
  strings are not.
- **Multiple logging libraries in one package.** A package importing two different logging
  libraries produces inconsistent formats and doubles the surface area. Flag as a `concern:`.
- **Multiple logger instances where one (or subloggers) would suffice.** Creating a new
  logger per function instead of deriving a child logger with added context defeats the point
  of structured logging. Prefer a base logger with `.With()` or equivalent fields per-scope.

### Dependencies
When a new dependency appears:
1. Does it solve a real problem, or is it convenience for a few lines of code?
2. Is it the canonical/maintained library, not a wrapper around something deprecated?
3. What's the dependency footprint? A 3 MB transitive tree for a 50-line problem is worth
   questioning.
4. Are versions pinned and current? Version pins written from memory are routinely stale.

---

## Part 5: Language-Specific Guidance — Go

These conventions apply to Go code reviews. They are grounded in the canonical style
references, in priority order:

1. **Effective Go** — https://go.dev/doc/effective_go
2. **Go Code Review Comments** (Go Wiki) — https://go.dev/wiki/CodeReviewComments
3. **Google Go Style Guide** — https://google.github.io/styleguide/go/

When citing a concern, link to the relevant section rather than asserting personal opinion.
"Per Go Code Review Comments §Interfaces: ..." is more credible and less confrontational
than "I think interfaces should be...".

### Interface placement
Go interfaces belong at the **consumer**, not the producer. The Go Wiki is explicit: define
interfaces in the package that *uses* the behavior, not the one that *implements* it.

In practice:
- A package that provides functionality exports a **concrete struct with methods**.
- The consumer defines the narrow interface it needs, typically unexported:
  ```go
  // in the consumer package
  type dataStore interface {
      Get(ctx context.Context, id string) (*Thing, error)
  }
  ```
- This enables testing without requiring the producer to anticipate every consumer's needs.

If the codebase already places interfaces at the producer (a common pattern in codebases
influenced by Java/C#), note it but don't block the MR unless the author is introducing a
*new* producer-side interface.

### Naming
- **No `Impl` or `Interface` suffixes.** Name types after what they are, not their role in a
  type hierarchy Go doesn't have.
- **Initialisms**: all-caps or all-lowercase (`userID`, `httpClient`, `XMLAPI`), never
  title-cased (`userId`, `XmlApi`).
- **Getters** omit the `Get` prefix: `Counts()` not `GetCounts()`. Use `Fetch` or `Compute`
  when the operation is expensive or involves I/O.
- **Receiver names**: 1–2 letters, consistent across all methods on a type. Never `self` or
  `this`.
- **Variable name length** proportional to scope size. Single-letter is fine in a 3-line
  block; large scopes need descriptive names.

### Error handling (Go-specific)
- Error strings: lowercase, no punctuation, suitable for wrapping
  (`fmt.Errorf("fetch user: %w", err)`).
- Prefer `errors.As`/`errors.Is` over type assertions on errors — they handle wrapping.
- Do not use `panic` for routine errors. Reserve it for genuinely unrecoverable invariant
  violations.

### Context propagation
- `ctx context.Context` is always the first parameter.
- Do not store a Context in a struct. Pass it per-call.

### Duplicate types across layers
If request/response types in one package are field-for-field identical to types in another,
with manual translation and no transformation, that's premature abstraction. Flag as a
concern — suggest using the model types directly or ensuring the translation layer actually
adds value.

### Testing (Go-specific)
- Mock interfaces belong in the test file of the consumer, not in a shared `mock_*.go` file
  shipped with production code.
- Table-driven tests are idiomatic — use them for functions with multiple input/output cases.
- Use `t.Helper()` in test helper functions so failure messages point to the right line.
- Test names: `TestFunctionName_Scenario_ExpectedBehavior`.

### Concurrency (Go-specific)
- Goroutine leaks: goroutines launched without a shutdown path or context cancellation.
- Channel misuse: unbuffered channels that can deadlock, channels that are never closed.

---

## Part 6: Language-Specific Guidance — Python

These conventions apply to Python code reviews, particularly for data pipelines and
tooling.

### Type annotations
- All function signatures need parameter and return types. No bare `def f(x):`.
- Class attributes should be typed, preferably via `@dataclass` for data-holding classes.
- Prefer `X | None` over `Optional[X]` (works at runtime in 3.10+, or 3.9+ with
  `from __future__ import annotations`).
- `Sequence` over `list` for read-only params, `Mapping` over `dict`.
- `Any` should be intentional, not a shortcut.

### Toolchain standards
Python projects should use:

| Tool | Role | Notes |
|---|---|---|
| **`uv`** | Package/project management | Replaces pip, poetry, pyenv |
| **Ruff** | Linting + formatting | Replaces black, isort, flake8 |

Flag any MR that introduces `pip install` in CI, uses Poetry or pyenv, or adds a linter
other than Ruff. These aren't nits — toolchain divergence compounds fast across repos. If a
project hasn't migrated yet, that's fine, but new projects and new CI pipelines should use
the standard tools.

For Python version constraints in `pyproject.toml`: the constraint must match the target
runtime. If a job runs on a pinned runtime (e.g., a managed Spark cluster), the upper bound
should prevent features from a newer Python version from slipping in.

### Dependency versions
Versions written from memory are routinely stale. If the `pypi-version-check` skill is
installed, use it automatically on any dependency file change. If not, flag version pins and
recommend the author verify against PyPI. Obviously stale pins (current major is 3.x but
pinned to `~=1.2`) are a non-nit concern.

### Dependency decisions
Same principles as the general section: does it solve a real problem, is it the
canonical library, what's the footprint? For Python specifically: prefer official SDKs
(e.g., `google-cloud-storage`) over subprocess wrappers around CLI tools.

### Pipeline scope creep
Data pipeline MRs tend to grow. If a branch was scoped to one layer (e.g., ingestion),
improvements to a different layer (e.g., transformation) belong in a separate MR. Flag scope
creep constructively: "This looks like follow-on work — want me to note it as a task?"

### Concurrency (Python-specific)
- Thread safety of shared resources; GIL assumptions that break under `multiprocessing`.
- `asyncio` misuse: blocking calls in async functions, unawaited coroutines.

### Self-documenting queries
If a SQL query or data filter relies on a non-obvious invariant of the data (e.g., "null in
this column means unprocessed because the pipeline sets it before marking complete"), that
invariant must be stated in a comment adjacent to the query. A query that is correct but
opaque is a review concern.

---

## Part 7: Language-Specific Guidance — TypeScript / JavaScript

These conventions apply to TypeScript and JavaScript code reviews.

### Type safety
- Prefer TypeScript over plain JavaScript for any non-trivial project.
- Avoid `any` — use `unknown` when the type is genuinely unknown, then narrow.
- No `@ts-ignore` or `@ts-expect-error` without an adjacent comment explaining why.
- Prefer `interface` for object shapes that may be extended; `type` for unions, intersections,
  and computed types.
- Use `readonly` for properties and arrays that should not be mutated after construction.

### Async patterns
- Every `Promise` must be awaited, returned, or explicitly voided (`void promise`). Floating
  promises are a common source of unhandled rejections.
- Prefer `async/await` over `.then()` chains for readability.
- Error handling in async code: `try/catch` at the boundary, not around every await.
- Watch for `forEach` with async callbacks — it doesn't await. Use `for...of` or
  `Promise.all(items.map(...))`.

### Concurrency (JS/TS-specific)
- Node.js is single-threaded but not immune to concurrency bugs: race conditions in
  async flows, unguarded shared state between requests, event-loop blocking with
  synchronous I/O or CPU-heavy computation.

### Module and import hygiene
- No circular imports. They cause subtle bugs (undefined at import time) that are hard to
  trace.
- Prefer named exports over default exports — they're easier to refactor and search for.
- Side-effectful imports (`import './setup'`) should be rare and documented.

### Toolchain standards
- **Biome** or **ESLint** for linting (not both). If the project uses ESLint, that's fine —
  but new projects should prefer Biome for speed and unified formatting.
- **Package manager**: use whatever the project has (`package-lock.json` = npm,
  `pnpm-lock.yaml` = pnpm, `bun.lockb` = bun). Do not introduce a different lockfile.
- Flag `devDependencies` that should be `dependencies` (or vice versa) — incorrect placement
  causes production build failures or bloated deployments.

---

## Part 8: Kubernetes and Helm Reviews

Changes to Helm charts, `values.yaml` overrides, and Kubernetes manifests deserve careful
attention because mistakes here affect production directly and are easy to miss in code
review.

### What to check
- **Resource names across environments**: subscription names, topic names, service account
  names, and similar identifiers in override files should be consistent across regions (EU,
  JP, US, etc.) unless intentionally different. Diff the override files side by side if
  multiple regions are touched.
- **Image tags**: should reference a specific version or digest, not `latest`.
- **Resource limits**: CPU and memory requests/limits should be set. Missing limits mean the
  pod can consume unbounded resources.
- **Environment variables and config**: new env vars should be present in all relevant
  override files. A variable defined in the deployment but missing from a region's values
  file will silently use the default (or fail).
- **Secrets references**: do secret names and keys match what's actually in the cluster? A
  typo here causes a pod crash, not a build failure.
- **Health checks**: new services should have liveness and readiness probes. Changes to
  existing probes (thresholds, endpoints) should be intentional.

### Tone for k8s reviews
These files are easy to get wrong and hard to test locally. Frame review comments as "let's
double-check this" rather than "you got this wrong" — the author may not have access to all
environments to verify.

---

## Part 9: Protobuf Reviews

For gRPC services and Pub/Sub messages using Protocol Buffers:

- **Field numbers are forever.** Never reuse or change a field number. Removing a field
  should use `reserved` to prevent accidental reuse.
- **Additive changes only.** New fields should have sensible defaults (the zero value for the
  type). Renaming fields is a wire-compatible change but breaks generated code — treat it as
  breaking.
- **Package and service naming**: follow the project's established conventions. Inconsistency
  in proto package paths causes import confusion across languages.
- **Backward compatibility**: if both producer and consumer are deployed independently (common
  with Pub/Sub), a breaking schema change requires coordinated rollout. Flag any change that
  would cause a consumer to fail on messages produced by the old schema.

---

## Part 10: Writing MR/PR Content (Author Mode)

When generating MR content on behalf of the author:

### Title
- Imperative, present tense: "Fix row count in batch-insert" not "Fixed row count" or
  "Fixing row count."
- Reference the ticket if one exists, but lead with the human-readable summary.
- No process language ("Investigate and fix...", "Try to resolve...").

### Description
Structure: **context → what changed → why → how to verify**.
- Write from the perspective of what is changing and why, not the development journey.
- Be explicit about scope: if the MR intentionally skips something, say so.
- For breaking changes, note migration steps even on unreleased branches.
- List known limitations or follow-on work.

### Commit messages
If a `commit-msg` skill is installed, defer to it. Otherwise, follow Conventional Commits:

- Format: `type(scope): subject`
- Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `style`, `test`
- Subject: imperative, ≤72 chars, no trailing period.
- `!` after the type for breaking changes to existing released behavior (not for new features
  or unreleased code).
- Body states the final contribution, not the development narrative.
- This skill drafts commit messages only — it does not execute git commands.

---

## Part 11: Drafting Review Comments

When drafting an inline comment for the author to post:

1. **Be specific.** Reference the exact line, function, or construct — not "this section."
2. **Explain why.** Don't just say "rename this." Say why the current name is misleading or
   non-idiomatic, and what problem the rename prevents.
3. **Offer a concrete alternative.** "How about X?" is more useful than "this is wrong."
4. **Label the severity.** If it's a nit, say so. The author should know what they can
   decline without further discussion.
5. **Match the author's voice** if you have prior comments to calibrate on.

For complex feedback (proposing a refactor, an architectural alternative):
- Lead with a one-line summary.
- Explain the problem.
- Show the proposed solution in a code block.
- Note tradeoffs.

### Structured findings reports
For multi-finding output — a full MR review, an investigation report, a self-review
summary — format findings as a numbered outline rather than free prose. Numbered findings
are individually addressable ("F3 is fixed in commit abc1234"), sortable by severity at a
glance, and easy to drop or modify without rewriting surrounding text.

Use this template per finding:

```
## F1 [blocking] <short title>

**Where:** <file:line, function name, or hunk reference>

**Why it matters:** <concise explanation of the actual risk or correctness issue>

**Evidence:** <reasoning, with code excerpt, grep result, or git context if relevant>

**Suggested fix:**
```<lang>
<concrete code or one-liner>
```
```

Severity tags in brackets — `[blocking]`, `[concern]`, `[nit]`, `[question]`,
`[suggestion]` — using the vocabulary from Part 2. The bracket form is for outline
headers; inline review comments not part of a structured report continue to use the
colon-suffixed form (`blocking: ...`, `concern: ...`, `nit: ...`).

Rank findings most severe to least. Cap at the most important findings — an exhaustive
list of 30 nits is less useful than a focused list of, say, 6 blockers + 3 real
concerns + 1 nit. If the review surfaces more than ~10 substantive findings, that itself
is a finding ("F0 [blocking]: this MR is too large to review thoroughly; recommend
splitting").

This format is also the natural output of Self-Review Mode (Part 3) — running the
"Refactor seams" checklist (Part 4) typically produces a numbered list of seam
violations, each one a candidate finding.

### Tone
- Lead with what's working before raising concerns.
- Avoid "this is wrong" — prefer "this pattern has a downside: [specific problem]. An
  alternative would be [concrete suggestion]."
- Documentation, comments, and prompts deserve the same critical attention as code. If a
  comment is misleading or a doc is ambiguous, say so — and offer a revision.

---

## Part 12: Pre-Submission Checklist

Before finalizing a review, verify:

- Distinguish MR-introduced issues from pre-existing ones.
- Check whether patterns being flagged already exist in the codebase.
- Calibrate severities — nits are labeled as nits, blockers as blockers.
- Address security concerns or explicitly raise them as questions.
- Scope feedback to what this MR changes.
- Look for what's missing, not just what's present.
- Present findings without rendering an approve/reject verdict — let the human decide.
- If generating MR content: it reads from the author's perspective, not the dev journey.

If running in Self-Review mode (Part 3), also verify:

- The diff was read once forward (does the new code do everything the old code did?) and
  once backward (does the new code reference anything the old code never produced?).
- Every contract crossed by the refactor has at least one internal contract test pinning
  the wire — see Part 3, "Internal contract testing."
- The "Refactor seams" checklist (Part 4) was walked, with at least the mechanical greps
  for `=None`/`=False` near seams, boolean-returning functions whose return is discarded,
  and functions whose only callers are tests.
