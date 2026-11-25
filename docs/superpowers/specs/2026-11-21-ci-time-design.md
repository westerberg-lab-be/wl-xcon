# CI time: likely tests first, a cap on machines, quiet nights skipped

**Status: approved by the PI, 2026-11-21** (in the UI). His three answers are in §3 as he gave them, and
they bind. It is built right after the console build merges, as its own small build cut from `main`
(the PI: "Right after the merge"), on the branch `ci-time`.

**Provenance.** The recon was read-only, in the `console-build-2026-11-17` worktree. Nothing in this
design was run locally. Every number below comes from the CI logs cited with it, or is marked as an
estimate. Versions and behavior of coverage.py and GitHub Actions were read from their documentation
on 2026-11-21. Where a fact matters and was not checked against a primary source, it is marked
UNVERIFIED.

## 1. Intent

The mutation check is 99% of CI: 1,396 runner-hours in 7 days. On 2026-11-21 the PI chose three fixes.
This design says how each one is built without making any verdict less strict. A verdict of `caught`
must still mean that a test failed under the mutant and would also have failed in the full suite as it
runs today.

**Amended 2026-11-22** (the whole-build review's I1; the PI, asked in the UI: "Accept, and look for
leaks"). The build guarantees less than the sentence above. A quick catch fails with the mutant and
passes without it, in the same selected order. A test whose catch the full order masks (a test before
it leaves state that hides the mutant) is a leaky test, which the full suite would not flag either,
and through one a quick catch can be a mutant the full suite would call `SURVIVED`. The PI accepted
that and asked to look for leaks: whenever a mutant falls through to the full run, each selected
test's result in the selected run is compared with its result in the full run, and a test whose
results disagree is listed under `QUICK AND FULL DISAGREE`, with a count in the closing tally. It is
a listing, never a verdict change, and it costs no extra run. §6's "the map's accuracy can only change
speed" holds except in that case.

## 2. What the logs say (run 44746169158, 5 of its 36 successful shards: 1, 9, 14, 26, 27)

- **The unmutated runs cost more than expected.** Each shard runs one baseline and one restored suite
  for every module it touches. That is 4-10 runs a job, 641-810 s each, 6,474 passed and 179 skipped.
  It adds up to 0.84-1.88 h a job (mean 1.29 h), about 30% of each job. Fix 1 alone does not touch
  them. Q2 (§3) does.
- **Mutants:** 53-54 a job, mean 137-333 s, median 15-171 s. The slowest are caught by only 1 or 2
  failures, so `--maxfail=3` never trips and they run the whole suite (11-13.5 min). Fix 1 speeds up
  these mutants the most.
- **Wall time:** the jobs already queue. Shards started between 20:10 and 05:41, and the run took
  about 22 h from start to finish.

## 3. The PI's decisions (2026-11-21, in the UI)

Each question changed what gets built. They were put to him in plain terms. His answers, as he gave
them:

- **Q1.** When the quick run catches a mutant, should we re-run those same tests on the unbroken code
  before calling it caught? Without the re-run, a test that fails only when its neighbors are skipped
  would count as a catch for every mutant that selects it. The re-run costs about 10-40 s a mutant,
  roughly 5-20 h a sweep (an estimate). Options: confirm / trust the quick run.
  **The PI: "Confirm each catch".** A selected-run catch is re-run on the unbroken code before it is
  called caught.
- **Q2.** Should each machine run the unbroken suite once, with the coverage run serving as that run,
  plus one restored run at the end, instead of twice for every module it touches? Verdicts are
  unchanged. The estimate is a saving of about 40 h a sweep, about a third of what remains. Options:
  yes in this build / later, as a backlog line.
  **The PI: "In this build".** One unbroken run per job, which doubles as the coverage map, plus one
  restored run, instead of two per module.
- **Q3.** Should a "quiet" night also require wl-xtasks and wl-preproc to be unchanged, since their
  task files and readers feed our tests? Options: our `main` only / all three.
  **The PI: "All three unchanged".** A quiet night is skipped only when our `main`, wl-xtasks and
  wl-preproc are all unchanged since the last fully passing nightly.

## 4. Fix 1: likely tests first (`tools/mutate.py`, its plugin, `tools/mutation_gate.py`)

### 4.1 The map

The map is built once per job, before any mutant. `mutate.py --build-map PATH` runs the whole suite
once under coverage.py:
`python -m coverage run --rcfile=tools/suite_progress/coverage.ini -m pytest <SUITE_ARGV>`.
The run goes through `_run_suite`, so `WLX_SUITE_PROGRESS` is set. The welfare-manifest test needs that
variable to judge a sweep's file by its original text. The rcfile sets:

- `core = ctrace`, written out explicitly. The coverage docs say "the sysmon core does not yet support
  plugins, dynamic contexts..." (config page, read 2026-11-21). ctrace is the default on 3.13, but
  pinning it means a later Python cannot change that.
- `source = wl_xcon`, so only the package is measured.
- `patch = subprocess`, which is new in coverage 7.10.
- `context = ${WLX_COV_CONTEXT-}`. The config page documents this `${VAR-default}` substitution.

The existing progress plugin gets one more duty, inert unless coverage is running. When each test
starts (`pytest_runtest_logstart`), it calls `coverage.Coverage.current().switch_context(nodeid)` and
sets `os.environ["WLX_COV_CONTEXT"] = nodeid`. When the test finishes, it switches back to `""`.

- A test's setup, call and teardown are filed under its pytest node id, parametrization included.
  coverage's own `dynamic_context = test_function` records "a function whose name starts with test",
  not a node id.
- A child Python process started during the test inherits the variable, and `patch = subprocess`
  starts coverage in it (subprocess page, read 2026-11-21). So the child's lines carry the same id, as
  a static context. That the child resolves `${WLX_COV_CONTEXT-}` from its own environment, rather
  than receiving the parent's resolved setting, is UNVERIFIED; §6's test 4 is what settles it, and if
  it does not hold, children go unmapped, which costs only speed.
- Import-time and collection-time code falls in the `""` context.

The data is then combined. For each target, the map holds the union of contexts over the executed
lines of the target's bodies: every definition of the name, from its first body statement to
`end_lineno`, read with `ast` (the `def` and decorator lines run at import). It reads them with
`CoverageData.contexts_by_lineno` (data API, added in 5.0). Only labels that exactly equal a node id
the progress file saw count as tests. Anything else (`""`, a `|`-joined label, a child that lost its
environment) is counted and printed, but selects nothing.

The map is JSON: test ids, per-function test lists, and a hash of every `wl_xcon/` and `tests/` file.
`mutate.py --map PATH` refuses a map whose hash does not match the tree.

**Cost:** one full suite under coverage, about 1.5-3x a plain run, so roughly 17-34 min a job. That
range is UNVERIFIED (the coverage docs give no overhead figure). The map's log line prints its time,
and the first nightly measures it.

### 4.2 Per job (Q2)

The map's run is the job's baseline: it must be green, and the progress plugin must have recorded its
tests, exactly as each module's baseline must be today. The `mutate.py` calls that follow take their
baseline from the map and run no baseline or restored suite of their own. One restored suite runs at
the end of the job. Verdicts are unchanged.

### 4.3 Per mutant, with `--map`

Without `--map`, mutate behaves exactly as today. The gate always passes it when it built a map
(§4.5).

1. INERT and SKIPPED are decided before any suite runs, as today.
2. **No test recorded** (the selection is empty): run the full suite as today, tagged `[full: no test
   in the map]`. This covers code that runs only at import or only in an unmeasured child.
3. **Selected run.** The mutant is on disk with the sentinel, through `_run_suite`, with the same
   `limit` and `--maxfail=3`. The selected test files are positional arguments, so only those are
   collected. The ids go to the plugin in a file named by `WLX_SUITE_SELECT`; the plugin deselects the
   other tests and records how many it found. Node ids can reach 65 KB (XC-331), so they are never put
   on argv. **It counts as a catch only if at least one failure or error is named**, from the summary,
   or from the progress file if the run was killed. An exit code alone is not enough: a usage error
   (4) or "no tests" (5) is a harness fault, not a catch.
4. **Confirm the catch (Q1).** Restore the file, then run the same selected tests unmutated, up to and
   including the last one that failed. If they pass, the verdict is `caught [selected N of 6653]`. If
   they fail, the failure did not need the mutant (an order-dependent test). The mutant then goes to
   step 5, and the test is listed at the end under `SELECTION NOT INDEPENDENT`.
5. **Full run, exactly today's code path and today's verdicts.** This happens if the selected tests
   passed, if they were stopped at the limit before any failure, or if the confirmation failed. It can
   end `caught`, `SURVIVED` or `TIMED OUT`. A selected run never produces a `TIMED OUT` on its own,
   because the full suite might still catch the mutant on the way to the hang.

**Amended 2026-11-22** (the whole-build review's I2 and M3). The selected run and the confirmation are
stopped at `max(SUITE_TIMEOUT_SECONDS, LIMIT_FACTOR x` their tests' share of the map's run`)`, never
over `limit`. The share is the selected tests' own times in the map's run, plus the part of that run
no test held. The full run keeps `limit` (Call 2). Before this, a mutant that hung its own selected
tests waited out the whole limit twice. A selected run stopped before any failure still falls through,
so the shorter limit costs a full run, never a verdict. And a confirmation counts only if it ran every
test that failed in the selected run. §10's "the same limit" is superseded by this.

### 4.4 The verdict line

The verb column is unchanged (`caught` / `SURVIVED` / `TIMED OUT`), so existing readers still parse
it. A tag after the name says where the verdict came from:

- `[selected 41 of 6653]`
- `[full, after 41 selected passed in 6s]`
- `[full: no test in the map]`
- `[full: selected failed unmutated too]`

The tested names follow, as today. A closing tally gives each kind's count and seconds, so the first
nightly measures the fall-through rate. Tests that are not behavioral (for example, ones that read
source) still catch their mutants in the full run, which is why the full run after a survivor is not
optional.

### 4.5 `mutation_gate.run()`

`mutation_gate.run()` builds the map once, then passes `--map` to every `mutate.py` call in the job. It
builds a map only for a shard that holds at least `MAP_FROM = 15` functions. That number is an
estimate of where the map pays for itself: about 25 min against about 100 s saved a mutant. Smaller
shards run exactly as today. This matters for pushes, whose 12 shards are often small. A failed map
build fails the job loudly. The new rcfile goes into `GLOBAL`.

`coverage>=7.10` goes into the `dev` extra. The latest release is 7.17.4, Apache-2.0 (PyPI JSON, read
2026-11-21). It needs an ADR-0004 line: "per-test line contexts, so a mutant runs the tests that reach
it first". No pytest-cov: the switching it would do is the four lines of §4.1.

## 5. Fixes 2 and 3 (`ci.yml`)

- **Cap:** `max-parallel: 8` on `mutation-full` and `4` on `mutation`. The default is "GitHub will
  maximize the number of jobs run in parallel depending on runner availability" (workflow syntax, read
  2026-11-21). GitHub's limit is 20 concurrent jobs on Free, 40 on Pro and 60 on Team (Actions limits
  page, read 2026-11-21). The account's plan is UNVERIFIED: `gh api user` shows none to this token.
  The cap also applies to the pre-merge hand dispatch, since that is the same job.
  **Amended 2026-11-22** (the whole-build review's M9): the jobs run in the `westerberg-lab-be`
  organization, so the limit that matters is that organization's, shared by its repositories, and
  `gh api user` is the wrong probe. A hand dispatch is in no concurrency group, so one during a
  nightly runs 8 + 8 mutation jobs plus up to 4 for a push: 20, Free's whole limit.
- **Skip (Q3):** a new job, `nightly-plan` (schedule and dispatch), runs before `mutation-full`. It
  resolves wl-preproc's and wl-xtasks' head SHAs with `git ls-remote` (both repos are public) and
  outputs them. Every shard then checks out those exact SHAs. Today each shard checks out whatever head
  exists when it starts, and with a cap of 8 the last wave starts hours later. On `schedule` only,
  `nightly-plan` does an `actions/cache/restore@v6` (node24, read 2026-11-21) with `lookup-only: true`
  and `continue-on-error: true`, keyed `mutation-full-passed-<sha>-<preproc>-<xtasks>`.
  - `mutation-full` has `needs: nightly-plan` and runs unless that lookup succeeded with a hit.
  - A last job, `mutation-full-passed`, has `needs: [nightly-plan, mutation-full]`. It runs on
    schedule only, and only when every shard succeeded. It saves the key.
  - **It fails closed:** a cache error, an evicted entry (7 days unused) or a new SHA all mean the
    sweep runs. Manual dispatch never looks the key up.
  - A workflow-level `concurrency` group for `schedule` (`cancel-in-progress: false`) queues a nightly
    behind one still running. At about 15 h of wall time under the cap (an estimate), two nightlies
    would otherwise overlap, and neither would see the other's pass.
  - The `test` job still runs every night. It is cheap, and it catches drift in the neighboring
    repositories.

## 6. How soundness is argued and tested

The argument: a `caught` from the selection needs a named test that fails with the mutant and passes
without it, in the same selected order. Every other outcome goes to today's code path. So the map's
accuracy can only change speed. Under Q2, a tree left changed between one module's sweep and the next
is refused by the map's hash, where today the next module's baseline would have gone red.

The tests are end to end on toy suites, in the style of `_toy_mutant`:

1. A selected failure is a catch, and the full suite is never run (counted at `_run_suite`).
2. A mutant that survives the selection, caught by an unselected test, reads `caught [full, after
   ...]`. If nothing catches it, it reads `SURVIVED`.
3. A function with no recorded test goes straight to the full suite.
4. A function reached only through `subprocess.run([sys.executable, "-c", ...])` is mapped to that
   test and caught by the selected run. With the child's environment stripped, it is unmapped and
   still caught by the full run.
5. An order-dependent selected failure: the confirmation fails, and a mutant that the full suite does
   not catch reads `SURVIVED`, never caught. This is the test the whole argument rests on.
6. A selected run killed before any failure goes to the full run. A usage error with no named failure
   is not a catch.
7. A map built from another tree is refused. A real map build records an in-process test by its
   parametrized node id and leaves import-time code with no test.
8. Every suite run (map, selected, confirmation, full) has `WLX_SUITE_PROGRESS` set.
9. `mutation_gate` builds one map per job above `MAP_FROM`, and none below it.
10. `ci.yml` is read for the caps and for the wiring of the skip and the dispatch bypass.

CLAUDE.md requires proving a test can fail, and `tools/` is not in `RETURNS`. So each new test is shown
to fail against a hand-broken copy of the code it checks, and the copy is then thrown away. Nothing
runs `mutate.py` or `mutation_gate.py` on this repository locally.

## 7. Expected saving: an ESTIMATE, not measured (the first nightly measures it)

- **Today:** (1.29 h unmutated + 2.95 h mutants) a job x 36 jobs = **about 153 h a sweep**, from the 5
  sampled shards. 1,909 functions.
- **Fix 1 alone:** each mutant takes S (selected run) + C (confirmation) + f x 700 s (the share f that
  falls through to a full run). With S = C = 20 s and f = 10%, that is 110 s a mutant: 1,909 x 110 s =
  58 h. The unmutated runs stay at 46 h, the maps add 10-20 h and setup adds 4 h, for **about 125 h
  (-18%)**. The range is about 90-170 h for S and C at 10-40 s and f at 5-15%.
- **With Q2, as built** (the map run doubles as the job's baseline, and one restored run a job): the
  unmutated runs drop from 46 h to 7 h, for **about 85 h (-45%)**.
- **Fix 3** removes every unchanged night entirely. **Fix 2** saves nothing in runner-hours. It caps
  the share of the account's runners the check takes, at the cost of wall time: about 125/8 ≈ 16 h a
  sweep, or about 11 h with Q2. A big merge push capped at 4 could take 3 waves of up to 6 h.

## 8. Risks

- f is larger than guessed: the saving shrinks, but no verdict changes.
- Coverage slows tests that wait on the clock, and some suite tests have bounded waits. The map run
  uses `BASELINE_TIMEOUT_SECONDS`. A test that is red only under coverage fails the job loudly, never
  silently.
- Subprocess children that replace their environment go unmapped. That only costs speed.
- Floating pip dependencies are not in the skip key. This is accepted: a dispatch, and the next change
  to `main`, cover it.

## 9. Docs (in the same commits)

- `pitfalls.md`: "A subset is not the suite: a test that passes in file order can fail on its own. A
  selection may decide speed, never a verdict."
- `ci.yml` comments for the caps, the skip and the concurrency group.
- One line in CHECKPOINT's CI row.
- Backlog:
  - **XC-500** narrows to "waits on: the first nightly under selection". Its premise of 10-minute `web`
    mutants mostly goes, so it closes if no shard nears 6 h.
  - **XC-140** gets a note that selection shortens catches that hang late.
  - **XC-275** and **XC-312** do not change.
  - New lines only for what is deferred: the shard count to revisit once measured, `MAP_FROM` to tune,
    and each test that `SELECTION NOT INDEPENDENT` names.

## 10. Calls made without asking

- coverage.py only, no pytest-cov, with `ctrace` pinned. Contexts are switched by our own plugin.
- Children are mapped through an environment-variable static context. Unknown contexts are printed and
  select nothing.
- The selected run gets the same limit and stops at 3 failures.
- The verb column is unchanged, and the source is a tag.
- `MAP_FROM = 15`.
- A map build failure is fatal.
- The neighbor SHAs are pinned for every shard.
- A workflow concurrency group for scheduled runs.
- The cap applies to dispatch.
- `coverage` goes into `dev`.
- No change to the shard count until it is measured.
