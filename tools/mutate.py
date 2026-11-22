#!/usr/bin/env python3
"""Prove a test can fail, by breaking the code it claims to cover.

A test that passes whether or not the behaviour exists is worse than no test: it
reports safety it cannot provide. This script neuters one function -- replacing its
body with `return []` -- runs the suite, stopping it at its third failure
(`MUTANT_MAXFAIL`), and restores.

**It clears `__pycache__` around every step, and that is the whole reason it is a
script rather than three shell lines.** Doing this by hand left stale bytecode from
a previous mutation in place: the source was restored, the interpreter kept running
the broken version, and the suite reported failures against code that was already
correct. A false failure is merely alarming. The same staleness in the other
direction reports a **false pass** -- the mutation never actually ran, and a vacuous
test is pronounced sound. That is the failure this exists to prevent, so it cannot
depend on anyone remembering to clear a cache.

    python3 tools/mutate.py wl_xcon/check.py _unbounded_waits
    python3 tools/mutate.py --all wl_xcon/check.py
    python3 tools/mutate.py --all --only detect,_saccade wl_xcon/saccade.py

`--only` narrows `--all` to the names given, in `--all`'s own order, with the same
baseline before and restored suite after. It exists so `tools/mutation_gate.py
--shard` can cut one module's functions across CI machines (PI, 2026-09-30), and
it refuses a name `--all` would not list rather than skipping it: a target that was
never run must not read as one that was.

**The map** (`--build-map PATH`; CI-time design §4.1, approved by the PI 2026-11-21). One
run of the whole suite under coverage.py (`tools/suite_progress/coverage.ini`), with the
progress plugin filing each test's lines under its pytest node id, writes a JSON file:
every test id in run order, for each function of each `wl_xcon/*.py` the tests whose
context reached a line of its body (`_body_lines`), and a hash of every file under
`wl_xcon/` and `tests/` (`tree_hash`), so a map read against another tree is refused
(`load_map`). Its run is a baseline -- it must be green, and the plugin must have recorded
its tests -- so a test that is red only under coverage refuses the map, by name.

    python3 tools/mutate.py --build-map .mutation-map.json

**A label that is not a node id selects nothing.** Only a context exactly equal to an id
the progress file saw start is a test. Import and collection run under `""`; a coverage
run nested inside another joins labels as `outer|inner`; a child Python that replaced its
environment loses the id. Each is counted in the map's line and selects no test, because
a selection can only run tests: a function reached only that way falls through to the
full suite, which costs time and never a verdict.

**What it costs is an estimate, not a measurement**: one suite under coverage, guessed at
1.5-3x a plain run (the coverage docs give no figure). The map's line prints its own
time, and the first nightly under the CI-time build is the measurement.

**A mutant's likely tests first** (`--map PATH`; §4.3). With a map, each mutant first runs
only the tests the map says reach it, and a failure there is a catch only once those tests
pass on the unbroken code (the PI's Q1, "Confirm each catch"): a quick catch fails with the
mutant and passes without it, in the same selected order. Every other outcome runs the full
suite exactly as without a map. That is all a quick catch guarantees: a leaky test, whose
catch the full order would mask, can make a quick catch of a mutant the full suite would
call a survivor -- and the full suite would not flag that test either. The PI accepted
that on 2026-11-22 ("Accept, and look for leaks"), so each mutant that does run the full
suite has its selected tests' results compared across the two runs, and a test that
disagrees is listed under `QUICK AND FULL DISAGREE` (`mutate` gives the argument). The
map's run is the call's baseline (Q2, "In this build"): no baseline and no restored suite
run here, and the map is refused unless the tree still hashes as it did when the map was
built. The caller runs the one restored suite:

    python3 tools/mutate.py --all --map .mutation-map.json wl_xcon/check.py
    python3 tools/mutate.py --restored

How much this saves is an estimate; the closing tally prints each kind's count and
seconds, and the first nightly under the CI-time build is the measurement.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
#: The package the map covers, every `*.py` directly in it (Call 8 of the CI-time plan).
PACKAGE_DIR = "wl_xcon"

#: Written before a file is mutated and removed after it is restored. A `finally`
#: cannot survive SIGKILL, and a timeout kills -- which once left a neutered
#: `__exit__` on disk that was then committed and pushed, because the commit did not
#: re-run the suite. The sentinel makes the damage self-healing rather than silent:
#: the next run restores from it before doing anything else.
SENTINEL = ROOT / ".mutate-in-progress.json"


def _restore_any_interrupted_run() -> None:
    """Undo a mutation left behind by a killed process -- **only if it is still there**.

    Called before anything else, including the baseline, so a suite that looks red
    because of a stale mutation is repaired rather than reported.

    **It restores only when the file still matches the text this tool wrote.** A
    blind restore is worse than the problem it solves: run this in the background,
    edit the source while it holds a mutation, and healing would revert that work
    silently. Never edit source while this is running; this check is the backstop,
    not permission.
    """
    if not SENTINEL.exists():
        return
    saved = json.loads(SENTINEL.read_text())
    path = Path(saved["path"])
    current = path.read_text()
    if current == saved["mutated"]:
        path.write_text(saved["original"])
        print(f"restored {path} from an interrupted run\n")
    elif current == saved["original"]:
        pass  # someone already put it back
    else:
        # **Do not restore.** The file has changed since the mutation, so the
        # sentinel's copy is stale and writing it back would silently revert
        # whatever was done in between. This nearly happened: a harness run was
        # backgrounded, source was edited while it held a mutation, and a
        # self-healing restore would have thrown that work away without a word.
        print(
            f"WARNING: {path} changed since an interrupted mutation run.\n"
            f"  Not restoring -- the sentinel's copy is stale and would revert "
            f"live edits.\n"
            f"  Check for a stray `return` at the top of a function, then delete "
            f"{SENTINEL.name}.\n"
        )
        return
    SENTINEL.unlink()


def _clear_pycache() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


#: A mutated suite may not terminate. Neutering `Scheduler.record` stops the counts
#: advancing, so a test running a block to completion never sees it finish -- and the
#: suite hung until an outer timeout killed the whole harness, past its `finally`,
#: stranding a neutered module on disk (2026-09-01). The sentinel healed that; a time
#: limit on every suite run is the fix.
#:
#: **What a timeout means changed as the suite grew, and the verdict did not.** Set
#: when the suite ran in seconds (674 tests in 16 s on 2026-09-25, about eighteen times
#: under the limit), 300 s then held only hangs, and a hang was counted *caught*. By
#: 2026-10-09 CI's baselines ran 146-207 s, the limit only 1.45-2.05 times them, and a
#: caught mutant runs the baseline plus a few seconds per failing test while those
#: tests wait out their own bounded waits -- so the limit mostly caught slow suites:
#: 82 functions on `main` read caught with no test named, and 45 more finished within
#: 30 s of it (XC-275; CI runs `37919247229` and `37991004478`). Two changes answer that:
#:
#: - the limit is relative to the measured baseline, `LIMIT_FACTOR` times it and never
#:   under this floor (`_mutant_limit`), so a slower machine is not 10 s from a false
#:   catch;
#: - a killed suite is judged by what it had done, read from the progress file
#:   `tools/suite_progress/` writes as it runs: a test that failed before the kill is
#:   a catch, named; a kill before any test failed is `TIMED OUT`, which is not.
SUITE_TIMEOUT_SECONDS = 300

#: A mutant's suite is stopped after this many times the baseline's wall time.
LIMIT_FACTOR = 2

#: The unmutated runs -- the baseline and the restored suite -- cannot be caught, so a
#: limit on them only bounds a harness that would otherwise wait forever. Generous
#: because the mutants' limit is computed from the baseline: a baseline cut short
#: would leave nothing to compute it from.
BASELINE_TIMEOUT_SECONDS = 3600

#: The pytest plugin that records each test as it runs, in a directory of its own so
#: that putting it on the child's `PYTHONPATH` exposes nothing else in `tools/`.
PROGRESS_PLUGIN_DIR = Path(__file__).resolve().parent / "suite_progress"
PROGRESS_PLUGIN = "wlx_suite_progress"
#: The variable naming the plugin's file. Without it the plugin writes nothing.
PROGRESS_ENV = "WLX_SUITE_PROGRESS"
#: The plugin's two other variables (`wlx_suite_progress.CONTEXT_ENV` and `SELECT_ENV`,
#: spelled again here because this file loads by path and cannot import the plugin;
#: `tests/test_mutate.py` holds the two spellings equal). The running test's id, read by
#: `COVERAGE_RC`'s `context`; and the file listing the ids a run keeps.
CONTEXT_ENV = "WLX_COV_CONTEXT"
SELECT_ENV = "WLX_SUITE_SELECT"
#: The map's coverage configuration (CI-time design §4.1).
COVERAGE_RC = PROGRESS_PLUGIN_DIR / "coverage.ini"
#: What the map's hash covers (`tree_hash`): what a test can import or read.
MAP_DIRS = ("wl_xcon", "tests")

#: `-rfE` makes pytest end with one line per failed or errored test. **Keeping only
#: the last line is how the 2026-09-25 nightly went red on a flaky test without once
#: naming it** (run 36115579357: `1 failed, 673 passed` on 7 of 41 unmutated runs).
#: The message on each line is trimmed to the terminal width *unless* `CI` or
#: `BUILD_NUMBER` is set, which GitHub Actions does -- pytest 9.1.1,
#: `_pytest/terminal.py:_get_line_with_reprcrash_message` and
#: `_pytest/compat.py:running_on_ci`, read 2026-09-26 -- so CI gets it whole.
SUITE_ARGV = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE"]

#: How many failing tests a mutant's line names before it only counts the rest.
NAMED = 3

#: **A mutant's suite stops at its third failure** (`--maxfail=3`; XC-313, the PI's "Stop
#: at 3 failures", 2026-10-22, chosen with the nightly's 24 shards). A caught mutant ran
#: the whole suite however early its first failure came -- about four minutes a mutant in
#: CI, the baseline's own time (run 39749961790's shard 4) -- and twelve shards of 80-81
#: functions ran past GitHub's 360-minute job limit: 2, 8 and 7 of 12 cancelled on
#: 2026-10-20, 21 and 22 (runs 39395156378, 39571238638, 39749961790). How much this
#: shortens a shard is not measured; the first nightly under it is that measurement.
#:
#: **What a log loses**: the total failed count. A caught mutant's line reads at most
#: about `3 failed` (or `errors`), its `passed` counts only the tests run before the stop,
#: and failures after the third are not run, so a line can no longer show real failures
#: behind three errors. **What it keeps**: the verdict, and the tests behind the first
#: three failures, named by `_caught_by` from the short summary as before.
#:
#: **The mutant's run only.** The baseline and the restored run are there to show the
#: whole suite passing, and a red one names every test that failed -- how the 2026-09-25
#: flake was found. A stop there would name three.
#:
#: What pytest does with it, read on 2026-10-22 in the source of pytest 9.1.1, the
#: version CI installs (run 39749961790's log), and of 9.0.2, which do not differ here
#: (9.1.1 adds only a `--max-warnings` check to `pytest_sessionfinish`); the end-to-end
#: tests in `tests/test_mutate.py` run whichever pytest is installed, so CI checks its own:
#:
#: - `_pytest/main.py`, `Session.pytest_runtest_logreport`: every failed report counts,
#:   a call's, a setup's or a teardown's, and collection errors too
#:   (`pytest_collectreport` is the same function). A test failing in its call and its
#:   teardown counts twice, so a stopped run can name two tests, one of them twice.
#: - `pytest_runtestloop` checks after each test's whole setup-call-teardown, and raises
#:   `Session.Failed`; `wrap_session` makes that exit code 1. Non-zero, so caught, exactly
#:   as before. After a third collection error `Session.pytest_collectstart` raises the
#:   same `Failed` at the next collector, so collection stops there (exit 1, where
#:   collection errors otherwise exit 2). When the third came from the last module
#:   collected there is no next collector: `pytest_runtestloop` raises `Interrupted`, the
#:   exit is 2, and both banners print, `stopping after 3 failures` and `Interrupted: 3
#:   errors during collection`, before the stats line. Non-zero either way, so caught
#:   either way, XC-312's class unchanged (`tests/test_mutate.py` runs both).
#: - `_pytest/terminal.py`, `TerminalReporter.pytest_sessionfinish`: the short summary,
#:   then the `!!! stopping after 3 failures !!!` banner, then the stats line. So the
#:   stats line is still stdout's last (`_run_suite`'s summary), and the banner, which
#:   starts with neither `FAILED ` nor `ERROR `, is not read as a failure (`_failures`).
#:
#: **The limit (XC-275) reads the same.** pytest stops only *at* the third failure, so a
#: suite with no failure before the limit runs exactly as it did and is still `TIMED
#: OUT`; one with one or two is still caught by name; one with three runs no further
#: test and is read as a finished run -- unless something hangs after the stop. pytest
#: then tears down every fixture (`_pytest/runner.py`'s `runtestprotocol` passes no next
#: item once `shouldfail` is set), so a session or module fixture whose teardown hangs
#: still runs the suite to the limit, and the line reads `timed out after Ns, 3 failed
#: before it`, caught by name as before.
#:
#: **No xdist here.** `SUITE_ARGV` has no `-n`, `pyproject.toml` no `addopts`, and CI's
#: mutation jobs do not install pytest-xdist. Under it, the stop is less exact: its
#: controller counts the workers' failures as they report them and, at three, sends
#: each worker a shutdown that it reaches only after the tests it was already handed
#: (`xdist/dsession.py`'s `_handlefailures` and `triggershutdown`, `xdist/remote.py`'s
#: `pytest_runtestloop`, read in pytest-xdist 3.8.0), so more than three could be named,
#: which `_caught_by` already counts as `+N more`.
MUTANT_MAXFAIL = 3


class SuiteRun(NamedTuple):
    """One run of the suite: whether it passed, pytest's final line, one line per
    failure, and -- from the progress file -- how far it got.

    For a run that finished, `failures` are the short summary's lines (`_failures`).
    For one that was killed (`timed_out`), pytest never printed a summary, so they
    are built from the progress file instead, in the same `FAILED <nodeid>` /
    `ERROR <nodeid>` form, and `running` is the test that had started and not
    finished (`None` when the kill fell between tests), `running_for` how long it
    had been running.

    A `NamedTuple` rather than a dataclass: this file is loaded by path, outside
    `sys.modules` (`tests/test_mutate.py`, `mutation_gate._load_mutate`), and a
    dataclass with postponed annotations looks its module up there and fails.
    """

    passed: bool
    summary: str
    failures: list[str]
    timed_out: bool = False
    seconds: float = 0.0
    started: int = 0
    finished: int = 0
    running: str | None = None
    running_for: float = 0.0
    #: Every test that started, by node id, in the order it started.
    ran: tuple[str, ...] = ()
    #: From the plugin's `selected` line: how many ids the selection named, and how many
    #: of them were collected. `None` when no selection was asked.
    wanted: int | None = None
    selected: int | None = None
    #: pytest's exit code; `None` when the run was killed.
    returncode: int | None = None
    #: `(node id, seconds)` for each test that finished, from its start to its teardown,
    #: in run order (`Progress.took`).
    took: tuple[tuple[str, float], ...] = ()


def _suite_env() -> dict[str, str]:
    """This process's environment without anything that would make a suite measured, or
    labeled, or selected, by a run around this one (Call 9 of the CI-time plan).

    The nightly's map run executes this file's own tests, and those build toy maps: a
    coverage run inside a coverage run. Inherited, `WLX_COV_CONTEXT` would become the
    inner run's static context and every label `outer|inner`, matching no node id;
    `COVERAGE_PROCESS_START` or `COVERAGE_PROCESS_CONFIG` would start the outer
    measurement in the inner interpreter (coverage 7.17.4, `control.py:1490-1491` and its
    `.pth` file, read 2026-11-21); and `COVERAGE_FILE`, `COVERAGE_RCFILE`, `COVERAGE_CORE`
    and the rest bend a run's data or config (`config.py:659-732`). So every `COVERAGE_*`
    goes, with the plugin's own two, and each run then sets what it needs.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COVERAGE_") and key not in (CONTEXT_ENV, SELECT_ENV)
    }


def _run_suite(
    cwd: Path = ROOT,
    *,
    timeout: float = SUITE_TIMEOUT_SECONDS,
    maxfail: int | None = None,
    coverage_file: Path | None = None,
    paths: Sequence[str] = (),
    select: Sequence[str] | None = None,
) -> SuiteRun:
    """Run the suite once, with the progress plugin loaded, and stop it at `timeout`
    -- and, given `maxfail`, at that many failures (`MUTANT_MAXFAIL`; only `mutate`
    gives it).

    Given `coverage_file`, under `coverage run --rcfile=COVERAGE_RC`, its data written
    beside that path (`COVERAGE_FILE`; one file per process, combined by `build_map`).
    `paths` are pytest's positional arguments, so only those files are collected; `select`
    is written to a JSON file named by `WLX_SUITE_SELECT`, never to argv, and removed after.
    The environment is `_suite_env()`, so a run nested in a measured one starts clean.

    `cwd` is only ever the repository in use; it is a parameter so a test can run
    this, end to end, against a three-test suite rather than this one.
    """
    stop = [] if maxfail is None else [f"--maxfail={maxfail}"]
    _clear_pycache()
    handle, name = tempfile.mkstemp(prefix="wlx-suite-progress-", suffix=".jsonl")
    os.close(handle)
    progress = Path(name)
    chosen: Path | None = None
    env = _suite_env()
    env[PROGRESS_ENV] = str(progress)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROGRESS_PLUGIN_DIR), *filter(None, [os.environ.get("PYTHONPATH")])]
    )
    measure: list[str] = []
    if coverage_file is not None:
        env["COVERAGE_FILE"] = str(Path(coverage_file).resolve())
        measure = ["-m", "coverage", "run", f"--rcfile={COVERAGE_RC}"]
    start = time.monotonic()
    try:
        if select is not None:
            handle, name = tempfile.mkstemp(prefix="wlx-suite-select-", suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as listed:
                json.dump(list(select), listed)
            chosen = Path(name)
            env[SELECT_ENV] = str(chosen)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    *measure,
                    *SUITE_ARGV,
                    *stop,
                    "-p",
                    PROGRESS_PLUGIN,
                    *paths,
                ],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            killed_at = time.time()
            seen = _read_progress(progress.read_text(encoding="utf-8"), killed_at)
            return SuiteRun(
                False,
                f"timed out after {timeout:.0f}s",
                seen.failures,
                timed_out=True,
                seconds=time.monotonic() - start,
                started=seen.started,
                finished=seen.finished,
                running=seen.running,
                running_for=seen.running_for,
                ran=seen.ran,
                wanted=seen.wanted,
                selected=seen.selected,
                took=seen.took,
            )
        seconds = time.monotonic() - start
        seen = _read_progress(progress.read_text(encoding="utf-8"), time.time())
    finally:
        progress.unlink(missing_ok=True)
        if chosen is not None:
            chosen.unlink(missing_ok=True)
    output = result.stdout.strip().splitlines()
    return SuiteRun(
        result.returncode == 0,
        output[-1] if output else "no output",
        _failures(result.stdout),
        seconds=seconds,
        started=seen.started,
        finished=seen.finished,
        ran=seen.ran,
        wanted=seen.wanted,
        selected=seen.selected,
        returncode=result.returncode,
        took=seen.took,
    )


class Progress(NamedTuple):
    """What the progress file says a suite had done."""

    failures: list[str]
    started: int
    finished: int
    running: str | None
    running_for: float
    ran: tuple[str, ...] = ()
    wanted: int | None = None
    selected: int | None = None
    #: `(node id, seconds)` for each test whose `finished` line carries its time.
    took: tuple[tuple[str, float], ...] = ()


def _read_progress(text: str, now: float) -> Progress:
    """Read the plugin's JSON lines (`tools/suite_progress/wlx_suite_progress.py`).

    **The file, never pytest's terminal output.** A killed suite's output stops
    mid-line and carries no summary; the file holds one record per event, written
    as each test starts, fails and finishes. A test that fails in more than one
    phase is one failure, named once, as pytest's summary names it: `FAILED` for
    its call, `ERROR` for its setup or teardown.

    A kill can land while the plugin is writing, so **the last line alone** may be
    cut short and is dropped if it does not parse. Anywhere else a line that does
    not parse is a broken plugin, and raises.
    """
    started: dict[str, float] = {}
    finished: set[str] = set()
    took: dict[str, float] = {}
    failed: dict[str, str] = {}
    chose: dict = {}
    lines = [line for line in text.splitlines() if line.strip()]
    for n, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if n == len(lines) - 1:
                break
            raise
        if record["event"] == "selected":
            chose = record
            continue
        nodeid = record["nodeid"]
        if record["event"] == "start":
            started.setdefault(nodeid, record["at"])
        elif record["event"] == "finished":
            finished.add(nodeid)
            if "at" in record and nodeid in started:
                took.setdefault(nodeid, max(0.0, record["at"] - started[nodeid]))
        elif record["event"] == "failed":
            failed.setdefault(nodeid, "FAILED" if record["when"] == "call" else "ERROR")
    unfinished = [nodeid for nodeid in started if nodeid not in finished]
    running = unfinished[-1] if unfinished else None
    return Progress(
        failures=[f"{kind} {nodeid}" for nodeid, kind in failed.items()],
        started=len(started),
        finished=len(finished),
        running=running,
        running_for=now - started[running] if running else 0.0,
        ran=tuple(started),
        wanted=chose.get("wanted"),
        selected=chose.get("found"),
        took=tuple(took.items()),
    )


def _failures(stdout: str) -> list[str]:
    """The `FAILED ...`/`ERROR ...` lines of pytest's short test summary.

    **Only the summary is read.** A failing test's captured output is echoed above
    it, verbatim, so a line there that happens to start `FAILED ` is not a failure.
    """
    lines = stdout.splitlines()
    start = next(
        (n for n, line in enumerate(lines) if "short test summary info" in line), None
    )
    if start is None:
        return []
    return [line for line in lines[start + 1 :] if line.startswith(("FAILED ", "ERROR "))]


NAME_LIMIT = 200


def _short(test_id: str) -> str:
    """A test id cut to `NAME_LIMIT` characters, keeping the start (file and test).

    A verdict line names tests, and a parameter can make an id enormous: one bytes
    parameter became a 65,537-space id, the 66 KB line broke `gh run view --log`'s
    64 KiB line limit, and the rest of that shard's log vanished (XC-331).
    """
    if len(test_id) <= NAME_LIMIT:
        return test_id
    return f"{test_id[:NAME_LIMIT]}... ({len(test_id)} chars)"


def _caught_by(failures: list[str]) -> str:
    """What to append to a mutant's line: the tests that failed, by node id.

    On 2026-09-25 a flaky test added exactly one failure to 24 mutant runs. A mutant
    nothing really covers reads `1 failed` in that run and is reported `caught`; the
    name of the test that did the catching is the only thing that shows it.
    """
    if not failures:
        return ""
    names = [_short(line.split(" ", 1)[1].split(" - ", 1)[0]) for line in failures]
    shown = ", ".join(names[:NAMED])
    rest = len(names) - NAMED
    return f"  <- {shown}" + (f", +{rest} more" if rest > 0 else "")


def _function_names(source: str) -> list[str]:
    """Every function, module-level and method alike, and each name once.

    Four blind spots found by using it, all the same shape -- the tool quietly
    examining nothing and reporting success. First it matched only `_`-prefixed
    names, so a run over `simulate.py` covered none of it. Then it matched only
    module-level `def`, so `record.py` -- which is entirely methods -- reported
    nothing to mutate, which reads like nothing to check. Then, found by replacing
    the pattern with the parser: `^ *def ([a-z_][a-z0-9_]*)\\(` cannot spell a
    capital, so **`photometry._XYZ` and `task.FixPoint` had never been mutated
    once** and no output said so -- they were simply not on the list.

    A coverage tool that can silently cover nothing has the exact failure mode it
    exists to catch, so `--all` refuses an empty target list and this asks the
    parser rather than a pattern.

    **And each name once.** Every definition of a name is neutered together, so a
    name six worlds implement ran six identical sweeps against identical inputs for
    identical results -- and a sweep is a full run of the suite. `run.py` was
    twenty-four targets and is twelve; `dio.py` was fourteen and is six.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = sorted(
        (node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return list(dict.fromkeys(name for _, name in found))


#: Returned by `mutate` when the neutered body is the body it already had.
INERT = "inert"

#: Returned by `mutate` when the suite was stopped at its limit before any test had
#: failed: nothing was seen to notice the mutation, so it is not a catch. Fatal, like
#: a survivor, and listed at the end with the test that was running (XC-275).
TIMED_OUT = "timed out"


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _neuter_source(source: str, function: str, returns: str) -> tuple[str | None, str]:
    r"""`source` with every definition of `function` returning `returns` first, or
    `(None, why)` when there is nothing this can safely do.

    **This asks the parser where a body starts.** It used to ask a regex, and that
    regex was wrong three times in three different ways. A trailing comment defeated
    the match, so `def __repr__(self) -> str:  # pragma: no cover` aborted a whole
    sweep. A body on the signature's own line matched, and the line inserted after it
    made a `SyntaxError`. And a default argument containing a `)` ended the signature
    early: `\([^)]*\)` stops inside `Params()`, so `saccade.detect` could not be
    found at all, and in `calibration.recenter` the scan for the colon ran on into
    `why: str` and inserted a statement **into the parameter list**.

    That last one is the seventh time this harness has been wrong and the third that
    broke toward a false *clean*: the suite reported collection errors, `mutate` read
    the non-zero exit as the mutation being caught, and a function that had never in
    its life been mutated was reported covered. The nightly on `main` still prints
    `caught recenter  2 errors in 0.82s`, which is what that looks like from outside.

    A parser cannot make any of those three mistakes, because `body[0]` **is** the
    body. Two decisions are left, and both are about where the line goes rather than
    about where the body is:

    - a docstring is stepped over rather than displaced, so the mutation lands below
      it and the function keeps its documentation;
    - a body written on the signature's own line is refused *for that definition*,
      because no line can follow `def deliver(self, ml: float) -> None: ...` and still
      parse. Its siblings are still neutered -- a Protocol stub beside a real
      implementation must not exempt the implementation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, f"{function}: source does not parse ({exc})"

    lines = source.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []
    on_signature_line = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function:
            continue
        anchor = node.body[0]
        if lines[anchor.lineno - 1][: anchor.col_offset].strip():
            on_signature_line += 1
            continue
        at = anchor.end_lineno if _is_docstring(anchor) else anchor.lineno - 1
        insertions.append((at, " " * anchor.col_offset + f"return {returns}\n"))

    if not insertions:
        if on_signature_line:
            return None, f"{function} is written on its signature's own line"
        return None, f"could not find {function}"

    # Descending, so an earlier insertion cannot move a later one's line number.
    for at, text in sorted(insertions, reverse=True):
        if at and not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        lines.insert(at, text)
    return "".join(lines), ""


def _already_inert(source: str, function: str, returns: str) -> bool:
    """True when inserting `return {returns}` at the top of `function` changes nothing.

    **A function that already returns immediately cannot be neutered, because it is
    already neutral.** `Quiet.display` and `Scripted.display` are `return None`;
    `World.display` is a docstring. Neutering inserts `return None` above a body that
    was `return None`, the suite passes because nothing changed, and the harness
    called that a SURVIVOR -- so the mutation gate could never go green, and the
    build failed on three functions that are not defects. `docs/CHECKPOINT.md` had
    been carrying the discrepancy as a footnote ("the only non-caught entries are
    no-op display bodies") rather than as the tool bug it is.

    **This must stay narrow.** It is the fifth time this harness has been changed,
    and the previous four were all it quietly examining nothing and reporting
    success -- so a category that does not fail the build is exactly the shape of the
    recurring bug. The condition is therefore proved from the AST, not guessed: every
    definition of the name must already begin (after any docstring) with the very
    statement the mutation would insert, or be empty where the insertion is
    `return None`. If any one definition would really be changed, this is False and a
    survivor is a real survivor.
    """
    try:
        tree = ast.parse(source)
        wanted = ast.dump(ast.parse(f"return {returns}").body[0])
    except SyntaxError:
        return False

    found = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function:
            continue
        found = True
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # the docstring is not a statement the mutation displaces
        neutral_already = returns.strip() == "None" and (
            not body
            or isinstance(body[0], ast.Pass)
            or (
                isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and body[0].value.value is Ellipsis
            )
        )
        if neutral_already:
            continue
        if body and ast.dump(body[0]) == wanted:
            continue
        return False
    return found


#: Where a verdict under `--map` came from (CI-time design §4.4): the kind each mutant is
#: tallied under, and, but for `FULL_AFTER`, whose tag names what the selection did, the
#: tag on its line.
SELECTED = "selected"
FULL_AFTER = "full, after selected"
FULL_NO_TEST = "full: no test in the map"
FULL_NOT_INDEPENDENT = "full: selected failed unmutated too"
#: The tag, tallied as `FULL_NOT_INDEPENDENT`, of a confirmation that passed without
#: running a test the selected run saw fail (the review's M3), so the tag does not say
#: "failed" of a test that never ran.
FULL_NOT_RERUN = "full: selected failure not rerun unmutated"
_KINDS = (SELECTED, FULL_AFTER, FULL_NO_TEST, FULL_NOT_INDEPENDENT)


class Tally:
    """Each kind's count and the seconds its mutants' suites took, for the closing lines
    under `--map`: the first nightly reads the fall-through rate from them (§4.4).

    `not_independent` lists each mutant whose selected failure came back on the unbroken
    code, `"name (id, id)"`; `missing` counts selected ids a run did not collect (Review
    Focus 3: counted, never fatal). `disagree` lists each selected test whose result in
    a mutant's selected run and in its full run differ, `"id (selected: R, full: R;
    mutating name)"` (`_disagreements`): a listing, never a verdict.
    """

    def __init__(self) -> None:
        self.count = dict.fromkeys(_KINDS, 0)
        self.seconds = dict.fromkeys(_KINDS, 0.0)
        self.not_independent: list[str] = []
        self.missing = 0
        self.disagree: list[str] = []

    def add(self, kind: str, seconds: float) -> None:
        if kind not in self.count:
            raise ValueError(f"no tally kind {kind!r}")
        self.count[kind] += 1
        self.seconds[kind] += seconds

    def lines(self) -> list[str]:
        return [
            *(f"{kind}: {self.count[kind]} in {self.seconds[kind]:.0f}s" for kind in _KINDS),
            f"selected ids not collected: {self.missing}",
            f"quick and full disagree: {len(self.disagree)}",
        ]


def _selected_files(ids: Sequence[str]) -> list[str]:
    """Each id's file, the part before its first `::`, once each, in the order the ids
    first name it. The ids arrive in the map's run order, so the files keep the
    baseline's relative order (Call 4)."""
    return list(dict.fromkeys(test_id.split("::", 1)[0] for test_id in ids))


def _hold(path: Path, original: str, mutated: str) -> None:
    """Put the mutation on disk, and then the sentinel that can undo it."""
    path.write_text(mutated)
    # Written *after* the mutation, holding both texts: the restore checks the
    # file still looks like what it wrote before putting the original back.
    SENTINEL.write_text(json.dumps({"path": str(path), "original": original, "mutated": mutated}))


def _release(path: Path, original: str) -> None:
    path.write_text(original)
    SENTINEL.unlink(missing_ok=True)
    _clear_pycache()


def _judge(run: SuiteRun, of: int | None) -> tuple[bool | str, str]:
    """A full mutant run's verdict and text, exactly as `mutate` has read one since
    XC-275: a stop before any failure is `TIMED_OUT`, a stop after one is a catch."""
    if run.timed_out:
        if not run.failures:
            return TIMED_OUT, f"{run.summary}, no test failed before it; {_how_far(run, of)}"
        return True, (
            f"{run.summary}, {len(run.failures)} failed before it; {_how_far(run, of)}"
            + _caught_by(run.failures)
        )
    return not run.passed, run.summary + _caught_by(run.failures)


def _failed_ids(run: SuiteRun) -> list[str]:
    """The tests of `run.ran` behind its failure lines, in run order. Matched against
    the ids the progress file saw, never split out of the line, so an id holding
    `" - "` is still found; a collection error names a file, which ran no test."""
    found = []
    for test_id in run.ran:
        for line in run.failures:
            named = line.split(" ", 1)[1] if " " in line else ""
            if named == test_id or named.startswith(f"{test_id} - "):
                found.append(test_id)
                break
    return found


def _results(run: SuiteRun) -> dict[str, str]:
    """Each test `run` gave a result, by node id: `failed` when a failure line names it
    (`_failed_ids`), else `passed` -- it finished and nothing failed, a skip included. A
    test still running when the run was killed had no result, and is left out."""
    failed = set(_failed_ids(run))
    return {
        test_id: "failed" if test_id in failed else "passed"
        for test_id in run.ran
        if test_id in failed or test_id != run.running
    }


def _disagreements(picked: SuiteRun, full: SuiteRun, function: str) -> list[str]:
    """Each test of the selected run `picked` that the full run `full` also gave a
    result, where the two results differ, in `picked`'s order: `"id (selected: R, full:
    R; mutating function)"`, the id cut by `_short` (XC-331's 65 KB id).

    **Looking for leaks** (the PI, 2026-11-22, "Accept, and look for leaks"). Both runs
    hold the same mutant, so a test whose result differs between them depends on what
    ran before it: a leaky test. A selected catch is confirmed only in the selected
    order, so a leaky test is the one way it can differ from the full suite's verdict.
    That catch cannot be compared without a full run it was spared, but its tests are
    the same ones that disagree here, on any mutant that does fall through. Free, since
    both runs happened; a listing, since the verdict is the full run's either way.
    """
    after = _results(full)
    return [
        f"{_short(test_id)} (selected: {before}, full: {after[test_id]}; mutating {function})"
        for test_id, before in _results(picked).items()
        if test_id in after and after[test_id] != before
    ]


def _up_to_last_failure(run: SuiteRun, selection: Sequence[str]) -> list[str]:
    """What the confirmation runs (Q1): the selected run's tests up to and including
    the last one that failed, in the order they ran. When no failure names a test that
    ran (collection errors only), the whole selection."""
    failed = set(_failed_ids(run))
    last = max((n for n, test_id in enumerate(run.ran) if test_id in failed), default=None)
    return list(run.ran[: last + 1]) if last is not None else list(selection)


def mutate(
    path: Path,
    function: str,
    args_returns: str,
    *,
    limit: float = SUITE_TIMEOUT_SECONDS,
    of: int | None = None,
    selection: Sequence[str] | None = None,
    tally: Tally | None = None,
    selected_limit: float | None = None,
) -> tuple[bool | str | None, str]:
    """`(verdict, the line's text)` for neutering `function`.

    The verdict is True if the suite failed, i.e. the function is covered; False if
    it passed (a survivor); `INERT` or `None` if nothing could be neutered; and
    `TIMED_OUT` if the suite ran past `limit` before any test failed. A suite that
    ran past `limit` *after* a test failed is a catch, and the line names the tests
    that failed and the one still running (`_how_far`, where `of` is the baseline's
    test count). The suite stops at its third failure (`MUTANT_MAXFAIL`, since
    2026-10-22), so a caught line counts at most about three: the total is gone, and
    the tests behind those three are named.

    A name defined more than once -- `satisfied`, implemented by every `World` --
    has **all** its definitions neutered together. Bailing on the ambiguity was the
    earlier behaviour and it was worse than useless: it stopped the whole run, so
    `run.py` reported nothing at all rather than reporting what it could.

    **What the neutered body returns changes how sharp the answer is.** For the
    checkers, whose results are concatenated, `return []` fails exactly the tests
    that cover that check -- so "1 failed" localises the coverage. `return None`
    breaks the concatenation instead, failing every check test at once: still proof
    the function is load-bearing, but no longer proof that any single test isolates
    it. Prefer the value the caller actually composes; the default suits list
    returns because that is what this codebase's checkers do.

    **With a `selection`** (`--map`; CI-time design §4.3), the map's tests for this
    function run first, and the text starts with a tag saying where the verdict came
    from (§4.4). **What a verdict from the selection guarantees** (§6, as the PI accepted
    it on 2026-11-22): a quick catch names a test that fails with the mutant and passes
    without it, in the same selected order. Every other outcome -- the selected tests
    passed, were stopped at their limit before any failure, named no failure at all, or
    failed again unmutated -- runs the full suite, through exactly the code a run
    without a map takes, to exactly its verdicts. So the map's accuracy changes only how
    long a verdict takes, **but for one case**: a leaky test, whose catch the full order
    masks (a test before it leaves state that hides the mutant), is a quick catch where
    the full suite would read `SURVIVED`. The full suite would not flag that leaky test
    either. It is looked for, never ruled out: each mutant that runs the full suite after
    a selection has every selected test's result compared across the two runs, and a
    test that disagrees is listed under `QUICK AND FULL DISAGREE` (`_disagreements`),
    without touching the verdict and without an extra run. The steps:

    - **No test in the map** (`selection` empty): the full suite at once, `[full: no
      test in the map]`. Code that runs only at import, or only in a child that lost
      its environment, lands here.
    - **The selected run**: the mutant on disk with its sentinel, `--maxfail` as the full
      run has it, and `selected_limit`, its tests' share of the map's run (never over
      `limit`; `SuiteMap.selected_limit` says why that is verdict-safe). The selected
      files are pytest's positional arguments and the ids go in the file
      `WLX_SUITE_SELECT` names, never on argv (one id reached 65 KB, XC-331).
      **It is a catch only if a failure or error is named.** An exit code alone is not:
      a usage error (4) or "no tests ran" (5) is the harness, not a test. Ids not
      collected are counted in the tally, never fatal (Review Focus 3).
    - **The confirmation** (the PI's Q1, "Confirm each catch"): the file restored and
      the sentinel gone, the same files and the selected tests up to and including the
      last that failed, with **no `--maxfail` and the selected run's limit** (Call 6).
      Green, and having run every test the selected run saw fail (the review's M3), the
      verdict is `[selected N of M]` (`N` the run's `selected`, `M` is `of`). Green without
      one of them, it asked that test nothing: `[full: selected failure not rerun
      unmutated]`, listed as "not run unmutated". Red, the failure did not need the
      mutant -- a test that passes in file order and fails on its own -- so the mutant
      goes to the full suite, `[full: selected failed unmutated too]`, and the test is
      listed under `SELECTION NOT INDEPENDENT`. A confirmation that times out is not a
      confirmation, and is listed "(timed out)".
    - **The full suite**, after a selection that did not catch: `[full, after N selected
      passed in Ss]`, or, so a tag never says "passed" for a run that did not
      (Call 5), `timed out in Ss` or `ended with no test named (exit C) in Ss`. **A
      selected run never ends `TIMED OUT` on its own**: the full suite may still catch
      the mutant on its way to the hang.

    The sentinel is on disk exactly while the file is mutated: through the selected run
    and the full run, and not through the confirmation. `tally` takes each mutant's kind
    and the seconds all its suites took, and the tests the two runs disagree on; without
    one they are dropped. `selected_limit` is `main`'s `SuiteMap.selected_limit`; without
    one the selected run and the confirmation have `limit`, as the full run does.
    """
    original = path.read_text()
    if _already_inert(original, function, args_returns):
        return INERT, f"body is already `return {args_returns}`; nothing to neuter"
    mutated, why = _neuter_source(original, function, args_returns)
    if mutated is None:
        # A miss is reported, never fatal. Under `--all` an abort here stopped the
        # sweep at the first unmatchable signature, and every function *after* it
        # went silently unmutated -- which reads as a completed run. That is the
        # same false-clean failure this whole script exists to prevent, and it is
        # the fourth blind spot of exactly that shape.
        return None, why

    if selection is not None:
        return _mutate_selected(
            path, function, original, mutated, tuple(selection),
            limit=limit, of=of, tally=Tally() if tally is None else tally,
            quick=limit if selected_limit is None else min(limit, selected_limit),
        )
    try:
        _hold(path, original, mutated)
        return _judge(_run_suite(timeout=limit, maxfail=MUTANT_MAXFAIL), of)
    finally:
        _release(path, original)


def _mutate_selected(
    path: Path,
    function: str,
    original: str,
    mutated: str,
    selection: tuple[str, ...],
    *,
    limit: float,
    of: int | None,
    tally: Tally,
    quick: float,
) -> tuple[bool | str, str]:
    """`mutate` with a selection: §4.3's steps 2-5, as `mutate`'s docstring gives them.
    `quick` limits the selected run and the confirmation, `limit` the full run."""
    spent = 0.0
    picked: SuiteRun | None = None

    def full(tag: str, kind: str) -> tuple[bool | str, str]:
        # The mutant is on disk with its sentinel here, as `mutate` without a map has it.
        nonlocal spent
        run = _run_suite(timeout=limit, maxfail=MUTANT_MAXFAIL)
        spent += run.seconds
        if picked is not None:
            tally.disagree.extend(_disagreements(picked, run, function))
        verdict, text = _judge(run, of)
        tally.add(kind, spent)
        return verdict, f"[{tag}] {text}"

    try:
        _hold(path, original, mutated)
        if not selection:
            return full(FULL_NO_TEST, FULL_NO_TEST)
        files = _selected_files(selection)
        picked = _run_suite(
            timeout=quick, maxfail=MUTANT_MAXFAIL, paths=files, select=list(selection)
        )
        spent += picked.seconds
        if picked.wanted is not None:
            tally.missing += picked.wanted - (picked.selected or 0)
        chosen = picked.selected or 0
        if not picked.failures:
            if picked.passed:
                how = "passed"
            elif picked.timed_out:
                how = "timed out"
            else:
                how = f"ended with no test named (exit {picked.returncode})"
            return full(f"full, after {chosen} selected {how} in {picked.seconds:.0f}s", FULL_AFTER)

        _release(path, original)
        confirm = _run_suite(
            timeout=quick, paths=files, select=_up_to_last_failure(picked, selection)
        )
        spent += confirm.seconds
        # Confirmed only if every test that failed under the mutant ran again unmutated
        # (the review's M3): a confirmation that dropped one -- an id that differs from one
        # collection to the next -- passed without asking it anything.
        unasked = [test_id for test_id in _failed_ids(picked) if test_id not in confirm.ran]
        if confirm.passed and not unasked:
            tally.add(SELECTED, spent)
            return True, f"[selected {chosen} of {of}] {_judge(picked, chosen)[1]}"

        # Named as a verdict line names them (`_caught_by`), so a collection error in the
        # confirmation is listed by its file.
        again = [_short(line.split(" ", 1)[1].split(" - ", 1)[0]) for line in confirm.failures]
        if confirm.timed_out:
            again.append("timed out")
        # The tag says "failed unmutated too" only of a confirmation that failed.
        tag = FULL_NOT_RERUN if unasked and not again else FULL_NOT_INDEPENDENT
        again += [f"{_short(test_id)} not run unmutated" for test_id in unasked]
        if not confirm.passed and not confirm.failures and not confirm.timed_out:
            again.append(f"exit {confirm.returncode}")
        tally.not_independent.append(f"{function} ({', '.join(again)})")
        _hold(path, original, mutated)
        return full(tag, FULL_NOT_INDEPENDENT)
    finally:
        _release(path, original)


def _how_far(run: SuiteRun, of: int | None) -> str:
    """Where a stopped suite was: the test still running and for how long, and how
    many of the baseline's `of` tests had finished. One test running for most of the
    limit is a hang there; most tests done is a suite near its own time."""
    done = f"{run.finished} of {of} tests done" if of else f"{run.finished} tests done"
    if run.running is None:
        return f"no test running; {done}"
    return f"running {_short(run.running)} for {run.running_for:.0f}s; {done}"


def _mutant_limit(baseline_seconds: float) -> float:
    """How long a mutant's suite may run: `LIMIT_FACTOR` times the baseline's wall
    time, and never under `SUITE_TIMEOUT_SECONDS` (see there for why)."""
    return max(SUITE_TIMEOUT_SECONDS, LIMIT_FACTOR * baseline_seconds)


def _only(targets: list[str], only: str, path: Path) -> list[str]:
    """`targets` cut down to the names in `only` (comma-separated), in `targets`' order.

    **A name `targets` does not hold is refused, never skipped.** This is how
    `tools/mutation_gate.py --shard` splits one module across machines, and a name
    it passes that is not there -- renamed since, or a typo -- would otherwise be a
    target nobody ran in a log that reads as a finished sweep: the harness's
    recurring failure. A list naming nothing is refused for the same reason.
    """
    wanted = [name.strip() for name in only.split(",") if name.strip()]
    if not wanted:
        raise SystemExit(f"--only named no functions (got {only!r})")
    missing = [name for name in dict.fromkeys(wanted) if name not in targets]
    if missing:
        raise SystemExit(
            f"--only names what --all does not list in {path}: {', '.join(missing)}"
        )
    return [name for name in targets if name in wanted]


def tree_hash(cwd: Path = ROOT) -> str:
    """sha256 over every file under `MAP_DIRS`, path and bytes both, in sorted relative
    POSIX path order; `__pycache__` skipped, since a run writes it and it is not source.

    A map describes the tree it was built from. Under Q2 (the PI, 2026-11-21) no module's
    sweep has its own baseline any more, so a tree changed since the map -- an edit, or a
    mutation the heal declined to restore -- would otherwise be swept as if it were the
    tree the map's green run saw. Each file's path and length go in before its bytes, so
    no two trees can feed the hash the same stream.
    """
    digest = hashlib.sha256()
    found = []
    for top in MAP_DIRS:
        base = cwd / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if "__pycache__" in path.relative_to(cwd).parts or not path.is_file():
                continue
            found.append((path.relative_to(cwd).as_posix(), path))
    for relative, path in sorted(found):
        data = path.read_bytes()
        digest.update(f"{relative}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def _body_lines(source: str, function: str) -> set[int]:
    """The lines of every definition of `function`, each from its first body statement
    to its `end_lineno`, read with `ast` (CI-time design §4.1). The `def` and decorator
    lines are left out because they run at import, under no test; a body is what only a
    call runs. Every definition, because `mutate` neuters them all together."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            lines.update(range(node.body[0].lineno, node.end_lineno + 1))
    return lines


class SuiteMap(NamedTuple):
    """Which tests reach each function, from one green run under coverage.

    `tests` is every test the run started, in its order; `functions` maps a module's
    path (`"wl_xcon/x.py"`) and a function's name to indices into `tests`; `unmatched`
    counts the labels that were not a recorded node id, which select nothing. `seconds`
    and `started` are the run's, so a later call can take its baseline from the map;
    `took` is each test's own time in it, start to teardown, in `tests` order, so a
    selection's limit can be its tests' share (`selected_limit`). A `NamedTuple` for the
    reason `SuiteRun` gives.
    """

    tree: str
    summary: str
    seconds: float
    started: int
    tests: tuple[str, ...]
    functions: dict[str, dict[str, tuple[int, ...]]]
    unmatched: int
    took: tuple[float, ...] = ()

    def tests_for(self, module: str, function: str) -> tuple[str, ...]:
        """The node ids that reached `function` in `module`, in `tests` order; `()`
        when none did or the map does not know it."""
        return tuple(self.tests[i] for i in self.functions.get(module, {}).get(function, ()))

    def selected_limit(self, module: str, function: str, limit: float) -> float:
        """How long `function`'s selected run, and its confirmation, may take: `LIMIT_FACTOR`
        times its tests' share of the map's run, never under `SUITE_TIMEOUT_SECONDS`, and
        never over `limit`, the full run's (the whole-build review's I2, 2026-11-22).

        **Why it is not simply `limit`.** `limit` is `LIMIT_FACTOR` times the whole suite
        under coverage, and a mutant that hangs its own tests -- the XC-140 class, counts
        that stop advancing -- hangs exactly the tests the map selects. At `limit` the
        selected run waited out a whole-suite limit and the full run then waited out
        another, so one hang cost two. Scaled, the selected run gives up at about what
        its tests should take.

        **The share is its tests' own times** (`took`, from the progress file's `start` and
        `finished` lines), plus the part of the map's run that no test held -- start-up
        and collection, which a selected run pays again, in part. Not `seconds x N /
        started`: a test's time is not the suite's average, and a selection is the tests
        that reach one function, not a sample, so a count share could give a selection of
        slow tests less than they take. Under coverage, so no shorter than a plain run of
        them needs.

        **It cannot change a verdict.** A selected run stopped here before any failure
        falls through to the full run, at `limit`, through today's code: it is never
        `TIMED OUT` on its own. One stopped after a failure is judged on the failures it
        had, which a longer run would also have had, and still needs its confirmation.
        A confirmation stopped here is not a confirmation, and the full run decides. So
        a limit too short costs a full run, never a verdict.
        """
        chosen = self.functions.get(module, {}).get(function, ())
        unheld = max(0.0, self.seconds - sum(self.took))
        share = unheld + sum(self.took[i] for i in chosen if i < len(self.took))
        return min(limit, max(SUITE_TIMEOUT_SECONDS, LIMIT_FACTOR * share))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = self._asdict()
        record["tests"] = list(self.tests)
        record["took"] = list(self.took)
        record["functions"] = {
            module: {name: list(indices) for name, indices in names.items()}
            for module, names in self.functions.items()
        }
        path.write_text(json.dumps(record), encoding="utf-8")


def _red_baseline(run: SuiteRun) -> SystemExit:
    return SystemExit(
        "\n".join([f"suite is not green to begin with: {run.summary}", *run.failures])
    )


def _blind_baseline(run: SuiteRun) -> SystemExit:
    # The progress file is how a stopped suite is judged. A baseline that ran and
    # recorded nothing means the plugin is not loading, and every timeout after it
    # would be judged blind -- so the sweep refuses rather than starting.
    return SystemExit(
        f"the progress plugin recorded no test during the baseline "
        f"({run.summary}); a suite stopped at its limit could not be judged. "
        f"Check {PROGRESS_PLUGIN_DIR / (PROGRESS_PLUGIN + '.py')} loads."
    )


def _resolved(path: str) -> str:
    return os.path.realpath(path)


def build_map(out: Path, cwd: Path = ROOT) -> SuiteMap:
    """Run the whole suite once under coverage and write which tests reach which
    function to `out` (CI-time design §4.1-4.2).

    The run is the job's baseline, held to a baseline's rules: red (named, as a red
    baseline is), or blind (no test recorded), it is refused and no map is written. A
    test red only under coverage -- one that checks `sys.gettrace()`, or a bounded wait
    the tracer makes it miss -- therefore fails the job loudly, by name.
    """
    try:
        # Imported here, and only here in this file, so `mutate.py` without
        # `--build-map` runs where coverage is not installed.
        from coverage import CoverageData
    except ImportError:
        raise SystemExit(
            'the map needs coverage.py: pip install "coverage>=7.10" (the `dev` extra)'
        ) from None
    tree = tree_hash(cwd)
    scratch = Path(tempfile.mkdtemp(prefix="wlx-map-"))
    try:
        run = _run_suite(
            cwd, timeout=BASELINE_TIMEOUT_SECONDS, coverage_file=scratch / ".coverage"
        )
        if not run.passed:
            raise _red_baseline(run)
        if not run.started:
            raise _blind_baseline(run)
        # `patch = subprocess` makes every process write its own `.coverage.<suffix>`
        # (`parallel`, `config.py:574` in coverage 7.17.4, read 2026-11-21). They are
        # combined here, by reading each through the data API's
        # `CoverageData.contexts_by_lineno` (added in 5.0; `sqldata.py`, read 2026-11-21).
        root = Path(_resolved(str(cwd)))
        reached: dict[str, dict[int, set[str]]] = {}
        for part in sorted(scratch.glob(".coverage*")):
            data = CoverageData(basename=str(part))
            data.read()
            for measured in data.measured_files():
                try:
                    relative = Path(_resolved(measured)).relative_to(root).as_posix()
                except ValueError:
                    continue
                lines = reached.setdefault(relative, {})
                for line, labels in data.contexts_by_lineno(measured).items():
                    lines.setdefault(line, set()).update(labels)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    index = {test: n for n, test in enumerate(run.ran)}
    labels = {label for lines in reached.values() for seen in lines.values() for label in seen}
    functions: dict[str, dict[str, tuple[int, ...]]] = {}
    total = hit = 0
    for module in sorted((cwd / PACKAGE_DIR).glob("*.py")):
        relative = module.relative_to(cwd).as_posix()
        source = module.read_text(encoding="utf-8")
        lines = reached.get(relative, {})
        names = {}
        for name in _function_names(source):
            found = {
                index[label]
                for line in _body_lines(source, name)
                for label in lines.get(line, ())
                if label in index
            }
            names[name] = tuple(sorted(found))
            total += 1
            hit += bool(found)
        functions[relative] = names
    made = SuiteMap(
        tree=tree,
        summary=run.summary,
        seconds=run.seconds,
        started=run.started,
        tests=run.ran,
        functions=functions,
        unmatched=len(labels - set(index)),
        took=tuple(dict(run.took).get(test, 0.0) for test in run.ran),
    )
    made.save(out)
    print(
        f"map: {len(made.tests)} tests, {hit} of {total} functions reached by a test, "
        f"{made.unmatched} labels selected nothing, built in {run.seconds:.0f}s "
        f"({run.summary})"
    )
    return made


def load_map(path: Path, cwd: Path = ROOT) -> SuiteMap:
    """The map at `path`, refused when it is missing, does not parse, or was built from
    a tree other than `cwd`'s (`tree_hash`)."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        made = SuiteMap(
            tree=record["tree"],
            summary=record["summary"],
            seconds=float(record["seconds"]),
            started=int(record["started"]),
            tests=tuple(record["tests"]),
            functions={
                module: {name: tuple(indices) for name, indices in names.items()}
                for module, names in record["functions"].items()
            },
            unmatched=int(record["unmatched"]),
            took=tuple(float(seconds) for seconds in record["took"]),
        )
        if len(made.took) != len(made.tests):
            raise ValueError(f"{len(made.took)} test times for {len(made.tests)} tests")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SystemExit(f"cannot read the map {path}: {exc}") from None
    now = tree_hash(cwd)
    if made.tree != now:
        raise SystemExit(
            f"the map {path} was built from another tree (map {made.tree}, tree {now}); "
            f"rebuild it with --build-map"
        )
    return made


def restored(cwd: Path = ROOT) -> int:
    """One unmutated suite after the mutants, green or the sweep fails: 0 or 1.

    It prints `restored: <summary>` and every failure, by name: five of the seven red
    suites on 2026-09-25 were restores, not baselines. `main` runs it after its mutants
    without `--map`; with `--map` the caller runs it once, as `--restored`, after its
    last module (Q2: one restored run a job, not one a module).
    """
    run = _run_suite(cwd=cwd, timeout=BASELINE_TIMEOUT_SECONDS)
    print("\n".join([f"\nrestored: {run.summary}", *run.failures]))
    return 0 if run.passed else 1


def _relative(path: Path) -> str:
    """`path` as the map names a module: relative to `ROOT`, in POSIX form."""
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(ROOT)).as_posix()
    except ValueError:
        raise SystemExit(f"--map maps modules under {ROOT}; {path} is not one") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("function", nargs="?")
    parser.add_argument(
        "--build-map",
        metavar="PATH",
        type=Path,
        help="run the suite once under coverage and write which tests reach each function",
    )
    parser.add_argument(
        "--map",
        metavar="PATH",
        type=Path,
        help=(
            "with --all or a function: run each mutant's mapped tests first, confirming "
            "each catch unmutated; the baseline is the map's run, and no restored suite runs"
        ),
    )
    parser.add_argument(
        "--restored",
        action="store_true",
        help="run one unmutated suite, as a sweep's restored suite, and nothing else",
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--returns",
        default="[]",
        help="what the neutered body returns; see mutate() on why it matters",
    )
    parser.add_argument(
        "--only",
        metavar="NAME[,NAME...]",
        help=(
            "with --all: mutate only these of its targets, in its order; a name "
            "--all would not list is refused, never skipped"
        ),
    )
    args = parser.parse_args()

    if args.restored:
        if (
            args.path is not None
            or args.all
            or args.only is not None
            or args.map is not None
            or args.build_map is not None
        ):
            raise SystemExit("--restored runs the unmutated suite alone; give it nothing else")
        _restore_any_interrupted_run()
        return restored(ROOT)

    if args.build_map is not None:
        if args.path is not None or args.all or args.only is not None or args.map is not None:
            raise SystemExit("--build-map maps the whole suite; give it no module or --all")
        _restore_any_interrupted_run()
        build_map(ROOT / args.build_map)
        return 0

    if args.only is not None and not args.all:
        raise SystemExit("--only narrows --all's target list; give --all as well")

    if args.path is None:
        raise SystemExit("give a module path, or --build-map PATH")
    path = ROOT / args.path
    if args.all:
        targets = _function_names(path.read_text())
        if not targets:
            raise SystemExit(f"--all found no module-level functions in {path}")
        if args.only is not None:
            targets = _only(targets, args.only, path)
    else:
        if not args.function:
            raise SystemExit("give a function name or --all")
        targets = [args.function]

    _restore_any_interrupted_run()
    suite_map: SuiteMap | None = None
    tally = Tally()
    if args.map is not None:
        # Q2's guard (Call 3): no baseline runs here, so the map's run stands in for one,
        # and only for the tree it saw -- checked after the heal, before any mutant.
        module = _relative(path)
        suite_map = load_map(ROOT / args.map, ROOT)
        summary, seconds, started = suite_map.summary, suite_map.seconds, suite_map.started
        source = f"{summary} (the map's run, under coverage, {seconds:.0f}s)"
    else:
        baseline = _run_suite(timeout=BASELINE_TIMEOUT_SECONDS)
        if not baseline.passed:
            raise _red_baseline(baseline)
        if not baseline.started:
            raise _blind_baseline(baseline)
        source, seconds, started = baseline.summary, baseline.seconds, baseline.started
    # Under `--map`, the map run's own time (Call 2): never shorter than a plain
    # baseline's limit, so it can only turn a would-be TIMED OUT into a verdict.
    limit = _mutant_limit(seconds)
    # The stop is printed with the limit, so a log that reads `3 failed` says why.
    print(
        f"baseline: {source}; mutants stopped after {limit:.0f}s "
        f"({LIMIT_FACTOR} x this run's {seconds:.0f}s, never under "
        f"{SUITE_TIMEOUT_SECONDS}s) or at {MUTANT_MAXFAIL} failures "
        f"(--maxfail={MUTANT_MAXFAIL})"
        + (
            ""
            if suite_map is None
            else (
                f"; a mutant's selected run and its confirmation after {LIMIT_FACTOR} x "
                f"its tests' share of the map's run, never under {SUITE_TIMEOUT_SECONDS}s "
                f"(SuiteMap.selected_limit)"
            )
        )
        + "\n"
    )

    survivors = []
    skipped = []
    inert = []
    unsettled = []
    for name in targets:
        if suite_map is None:
            caught, summary = mutate(path, name, args.returns, limit=limit, of=started)
        else:
            caught, summary = mutate(
                path, name, args.returns, limit=limit, of=started,
                selection=suite_map.tests_for(module, name), tally=tally,
                selected_limit=suite_map.selected_limit(module, name, limit),
            )
        if caught is None:
            skipped.append(name)
            print(f"  SKIPPED   {name:32} {summary}")
            continue
        if caught is INERT:
            inert.append(name)
            print(f"  inert     {name:32} {summary}")
            continue
        if caught is TIMED_OUT:
            unsettled.append(f"{name} ({summary})")
            print(f"  TIMED OUT {name:32} {summary}")
            continue
        # **Caught only when `mutate` said exactly True.** The other verdicts are
        # strings, and a string is truthy: a fall-through `if caught` would read any
        # verdict this loop does not know -- a new sentinel, a misspelt one -- as a
        # catch, which is this harness's recurring failure in a new place.
        if caught is True:
            print(f"  caught    {name:32} {summary}")
        elif caught is False:
            print(f"  SURVIVED  {name:32} {summary}")
            survivors.append(name)
        else:
            raise ValueError(
                f"mutate() gave {name} the verdict {caught!r}, which is none of True, "
                f"False, None, INERT or TIMED_OUT; refusing to read it as anything"
            )

    if suite_map is None:
        ok = restored(ROOT) == 0
    else:
        ok = True
        print("\nrestored: left to the caller (--map)")
        print("\n".join(["\ntally (each kind's mutants, and the seconds their suites took):",
                         *(f"  {line}" for line in tally.lines())]))
        if tally.not_independent:
            # A test named here fails on its own, unmutated, in its selected order: a
            # selection cannot judge the mutants it reaches, so they ran the full suite.
            print(f"\nSELECTION NOT INDEPENDENT: {', '.join(tally.not_independent)}")
        if tally.disagree:
            # A listing, never a verdict (the PI, 2026-11-22, "Accept, and look for
            # leaks"): a test named here gave one result in a mutant's selected order
            # and another in the full order, under the same mutant, so it depends on
            # what ran before it. Each one is a leaky test to fix (`_disagreements`).
            print(f"\nQUICK AND FULL DISAGREE: {', '.join(tally.disagree)}")
    if inert:
        # Printed, never fatal, and never silent: these are functions no mutation
        # can reach, and a reader has to be able to tell that from coverage.
        print(f"\nNOT MUTABLE (already returns immediately): {', '.join(inert)}")
    if skipped:
        print(f"\nNOT MUTATED (signature not matched): {', '.join(skipped)}")
    if survivors:
        print(f"\nNOT COVERED: {', '.join(survivors)}")
    if unsettled:
        print(f"\nNOT SETTLED (timed out before any test failed): {', '.join(unsettled)}")
    if survivors or skipped or unsettled:
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
