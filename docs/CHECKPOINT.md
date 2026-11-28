# Where this build is

**Last updated 2026-11-28**, at the commit this file was committed in (`git log -1 -- docs/CHECKPOINT.md`). **This page
is the current state only, and stays one page** (the PI, 2026-11-07, after a whole-project audit). Every earlier
entry, 2026-08-31 to 2026-11-07, is in `docs/CHECKPOINT-history-2026-08-31-to-2026-11-07.md`, unchanged: read one
there only when this page or a backlog line sends you to it. A session ends by updating this page (CLAUDE.md).

## Read this, and no more
1. **This page.**
2. **`docs/backlog.md`**: everything open, one line per item; a decision for the PI is asked, never filed there.
3. **`CLAUDE.md`**: the conventions every session is bound by.
4. **The spec your work names** (`docs/superpowers/specs/2026-08-31-spec-map.md` says which) and architecture.md's section for your modules.

## The goal, and the four day-one legs
A real animal's session on a rig, its data read by wl-preproc, in January 2027 (hardware is bought at KU Leuven
from then). **Hardware verified: none.**

| Leg | Where it stands |
|---|---|
| DIO out | `dio.py`: the card's protocol, `Simulated` and `Absent` (refuses); no driver until the card is on the bench (pitfall P10). |
| Gaze in | UDP ingest, replay, calibration and online saccade detection (version 11, in step with wl-preproc's), no hardware. |
| Frame on screen | **Built on the `build-e` branch, unmerged**: the display process, its 120 Hz proof passed on wh-dws0's G8 with the GPU clocks locked (`docs/measurements/wh-dws0/2026-11-24-display-proof/` on that branch). |
| Reward out | End to end to a simulated pump, the day's total and its floor accounted; no pump calibration until the rig. |

**The session number** comes from the sync box (wl-sync's `/session` routes, since 2026-10-19); the PI, 2026-11-07:
if the box cannot be reached the rig refuses to open and says why. Its client is XC-460; until then the operator's.

## Where the branches are
**The repositories moved** to the `westerberg-lab-be` organization on 2026-11-21 (`f637bc99`; old URLs redirect).
- **`main` is xc505-warning's merge** (this page's commit), on the PI's "Merge it" (2026-11-28): the warning that
  an answer can come inside the saccade detector's first frames (XC-505; `answer-inside-saccade-warmup`), its two
  follow-ups XC-559 and XC-560. Evidence: push CI 45975481312 on `516d6c96`, tests on 3.11-3.13 and its 12
  mutation jobs read to their closing lines, all clean; welfare diff against `89f7b94`: nothing to read.
  Before it, **the CI-time build's merge** (`93b4cb05`, the PI's "Merge once quick CI passes", 2026-11-25). The mutation gate builds one coverage map per job of 15 functions or more, runs each mutant's likely
  tests first (each quick catch confirmed unmutated), caps the sweep at 8 machines, and skips a quiet night
  (`nightly-plan`). Evidence: full sweep 45458320469 on `bb70e7b2` read to every shard's closing line: 35 of 36
  clean, maps 1336-2428 s, shards median 112 min and at most 221; shard 35 found `web._rest_said` and `web._rest`
  (console-build functions) checked only by browser tests, which the sweep skips; pinned by two `test_web` tests
  (`0315b21b`), each hand-broken in a scratch copy and caught; push CI on `0315b21b` clean. Welfare diff against
  `89f7b94`: nothing to read. Before it: the console build (`293a66e1`, 2026-11-21).
  **wl-xtasks' main is `3de82c2`**; **wl-preproc's is `b75cc85`** (it accepts build E's `system` actor and counts
  the rig's pauses apart, `training_records.system_pauses`).
- **Built, unmerged, local, each already up to date with `293a66e1`** (merged 2026-11-24, each behind a full suite;
  ledgers in each worktree's `.superpowers/sdd/`). Each merges this main in, is pushed for CI, and merges on the PI's
  yes by name:
  - **`nightly-flakes`** (`2e164ef8`; at its merge its pitfall P30 is
    renumbered after this main's P30, and its backlog line added).
  - **`rig-readings`** (XC-434/435; `ba7ee97d`): animal ID and Nickname, `/health` readings for wl.works. At its
    merge: `out_since` to the PI as a numbered welfare item, the REFERENCE/demo line, M7 as a question, and the
    dialog's screenshots (`.superpowers/sdd/2026-11-21-rig-readings/merge-ask-shots/` in its worktree).
  - **`build-e`** (the display process; `77f030f8`): complete. 120 Hz proof passed, the fallback seen on screen,
    the PI's welfare approval of its ten items (2026-11-24; the `_hold` docstring corrected, no code changed). At
    its merge: the renumbers its `fix-report.md` lists, and wl-preproc pulled to `b75cc85` first.
  - **`drawings`** (the setting drawings; spec `docs/superpowers/specs/2026-11-20-setting-drawings-design.md` and
    plan on the branch): 15 tasks built, whole-build review APPROVE WITH FIXES, its fix wave running (the PI
    2026-11-25: the rig's draft check is made fast or turned off, never delaying a Stop or reward while paused).
  - **Numbers taken twice**: `link.SCHEMA` 24 (build-e, rig-readings, drawings), pitfall P30 and backlog IDs from
    XC-555 (rig-readings, build-e, nightly-flakes, drawings): each later merge renumbers past what main holds.

## What exists
- **Tasks**: the task object and its load check (`task.py`, `check.py`), engine builds A1 to C3; five reference
  tasks in `tasks/` are engine fixtures; real tasks come from wl-xtasks, written with the PI. **The first real task,
  the pop-out search**, is wl-xtasks' `tasks/popout_search.py` (its main, `64929fe`), tested here by path
  (`tests/test_popout_search.py`); its design `docs/superpowers/specs/2026-11-07-popout-search-design.md`. Two facts
  a session gets wrong: a crashed session's first run goes on from the run its process stopped in
  (`Session._going_on`), the previous session's end only *shown* at a start; a value held at a staircase's hardest
  level counts toward moving up.
- **Running**: the trial loop, daemon and CLI (`run.py`, `taskd.py`, `cli.py`); the simulated animal and eye.
- **The console**: a page on the LAN (`serve.py`, `web.py`, `link.py`), writes from the box only; wl.works sign-in
  waits on the rig's https page (XC-151, XC-152).
- **Demo mode** (`wlx demo <task>`; XC-013): the mouse as the eye (`pointer.py`), the exact drawer's frames on the
  page (`demoscreen.py`), `POST /gaze`, `wlx taskd --demo`, `wlx demo` (`demo.py`), and a demo from a live rig's page
  (`--demo-root`, `--link`'s fourth endpoint). Open: XC-506 (fidelity), XC-509 (the rig lock on macOS), XC-515
  (Ctrl-C raised where it lands in `wlx taskd` and `wlx serve`; `wlx demo` counts it, `demo.py`'s docstring).
- **Welfare** (`bounds.py`, `welfare.py`, `marks.py`, `stranded.py` and the functions architecture.md lists, held to
  `tests/welfare_manifest.json`): one limit, out of the cage to back in, eight hours (the institution's); the day's
  fluid is a floor; each delivery's size is a ceiling; a task names how reward is configured, never how much. **The
  record**: the session folder `xcon/` that wl-preproc reads (`record.py`).

## What is next (the PI, 2026-11-07 to 2026-11-21)
In order:
1. **The merges**, each on his yes by name after clean CI: nightly-flakes, rig-readings, build-e,
   then the drawings once its fix wave and re-review are done and he has tried it.
2. **Lab time: XC-391, XC-392, XC-393** (design approved, `.superpowers/brainstorm/lab-time-draft.md` in the main
   checkout).
3. **The XC-505 warning**'s follow-ups, if any, after its merge.
4. **Paradigms above tasks**, designed with wl.works.
5. **Side work on the January path**: the sync-box client (XC-460), the operator's guide (XC-014), the default limits
   file (XC-459, a welfare design with the PI), wl-preproc's C3 fixture (XC-510).
6. **Paused until the pop-out search has run on a rig**: engine builds C4 to C9 and the rig-edit build.

**How the work is run** (the PI, 2026-11-07): a plan is a design and a list of tests, built once; one review of the
whole build, and his welfare review of any welfare-listed change; side work only on the January path; a rare edge case
is settled from his stated principles and told to him in one line; every merge waits for his yes by name and clean CI.

## Working here
- **Suite**: `WLX_REQUIRE_PREPROC=1 WLX_REQUIRE_BROWSER=1 WLX_REQUIRE_XTASKS=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p
  no:cacheprovider -n auto` (a `wl-preproc` checkout or link at the root, at `d8fe06b` or later, and a `wl-xtasks`
  one, git-ignored at `/wl-xtasks`; wl-sync at the commit `wl-preproc/pyproject.toml` pins). **6717 passed on
  `main`**; none skipped.
- **CI**: each push sweeps the changed modules with mutants (`tools/mutation_gate.py`), the nightly all, in 36 shards,
  8 at once, skipping a night on which our `main`, wl-preproc and wl-xtasks are unchanged since the last full pass.
  A job of 15 functions or more builds one coverage map first, runs each mutant's mapped tests before the full suite
  (each quick catch confirmed unmutated) and one restored suite at its end; its tally, `SELECTION NOT INDEPENDENT`
  and `QUICK AND FULL DISAGREE` lists say how each verdict was reached (`tools/mutate.py --map`). A big branch runs
  the sweep by hand before its merge (`gh workflow run ci.yml --ref <branch>`). Read every shard's log to its closing
  line; the run's summary can be wrong.
- **The neighbors** are read weekly, by their handover files (the PI, 2026-11-07): wl-preproc, wl-sync, wl-works,
  wl-xtasks. Last read: 2026-11-07, by the audit; wl-preproc answered XC-491 and XC-502 on 2026-11-14 and its
  handover was read at `d8fe06b`. Open asks of each: `docs/pending-*-amendments.md`.
- **The welfare diff**: `tools/welfare_diff.py BASE HEAD`, item by item (`SAME`/`DIFFERS`), at every merge. **The
  session fuzzer**: `tools/fuzz_sessions.py`, read back by wl-preproc; `tests/test_fuzz_sessions.py` runs a little.
