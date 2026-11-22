# Where this build is

**Last updated 2026-11-21**, at the commit this file was committed in (`git log -1 -- docs/CHECKPOINT.md`). **This page
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
| Frame on screen | **Not built**: build E, the display process. Spike S: `docs/measurements/wh-dws0/2026-10-08-vulkan-timing/`. |
| Reward out | End to end to a simulated pump, the day's total and its floor accounted; no pump calibration until the rig. |

**The session number** comes from the sync box (wl-sync's `/session` routes, since 2026-10-19); the PI, 2026-11-07:
if the box cannot be reached the rig refuses to open and says why. Its client is XC-460; until then the operator's.

## Where the branches are
**The repositories moved** to the `westerberg-lab-be` organization on 2026-11-21 (`f637bc99`; old URLs redirect).
- **`main` is the console build's merge** (this page's commit), merged 2026-11-21 on the PI's "Merge it": the console
  settings build, the console interface rework (each setting a box with its own Apply, groups, percents, find and
  pins, telemetry schema 23), the summary-and-editor layout, the pop-out revision (TSK-78 to TSK-80, codes
  4139-4145), the pop-out mouse flake fix (pitfall P29), one whole-build review and its fix wave, and the PI's
  Safari try-out answers ("ring" everywhere; a too-dim panel starts at its reachable top with a warning; the
  Special trials line never cuts). Evidence: push CI 44954599943 clean; full sweep 44746169158 read to every
  shard's end (4 survivors, all fixed); the 97 changed functions no shard reached broken by hand, one at a time, all
  caught, each confirmed alone (`.superpowers/handbreak-web-2026-11-21/`); welfare diff against `89f7b94` all SAME.
  **wl-xtasks' main is `3de82c2`** (the revised pop-out); CI checks out its main again.
- **Built, unmerged, local** (each also bundled in `.superpowers/migration-2026-11-21/`; ledgers in each worktree's
  `.superpowers/sdd/`). Each merges the new main in before it is pushed:
  - **`xc505-warning`** (`7d53d9de`) and **`nightly-flakes`** (`cf256b5c`): built and reviewed; merge next, in that
    order, each on the PI's yes after CI (nightly-flakes at its merge: pitfall P30's number, its backlog line).
  - **`rig-readings`** (XC-434/435; `ab451a73`): every animal has an ID (element-animal's `subject`, at most 8
    lowercase) and a Nickname (at most 64), the PI's ruling of 2026-11-21; `/health` readings for wl.works. Built,
    reviewed, fix wave re-reviewed clean. Its push sweeps every module (it edits `mutation_gate.py`), so it is
    pushed only after the CI-time build. At its merge: `out_since` to the PI as a numbered welfare item.
  - **`build-e`** (the display process; plan `docs/superpowers/plans/2026-11-20-build-e.md` on the branch): Tasks
    1-7 done on OrbStack's Linux (lavapipe), standing in for wh-dws0. The PI, 2026-11-21: wh-dws0 may be used over
    SSH for off-screen tests; ask him again before any on-screen run. Next: Task 8, `direct` and `window` on
    wh-dws0.
  - **Telemetry schema**: build-e and rig-readings each take `link.SCHEMA` 24; the second to merge renumbers to 25.
- **The CI-time build** (the PI, 2026-11-21: "holding up other repos"; he chose likely tests first, a cap on machines
  at once, and skipping unchanged nights): design and plan approved, in `.superpowers/migration-2026-11-21/`
  (`ci-time-design.md`, `ci-time-plan.md`); cut `ci-time` from this main and build it next.

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
1. **The CI-time build**, then the merges of **xc505-warning** and **nightly-flakes**, then **rig-readings**; each
   merge on his yes by name after clean CI.
2. **Build E, the display**: Task 8 onward (`direct` on wh-dws0, the fault path with its welfare review, the desktop
   proof at 120 Hz); "done" includes a real screen.
3. **The setting drawings** (his ask in the try-out: a small schematic beside the setting opened; mock-up
   `.superpowers/brainstorm/71253-1795178057/`, notes `.superpowers/brainstorm/drawings-2026-11-20-notes.md`): a
   design with him, element by element. Then **lab time: XC-391, XC-392, XC-393**.
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
  one, git-ignored at `/wl-xtasks`; wl-sync at the commit `wl-preproc/pyproject.toml` pins). **6660 passed on
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
