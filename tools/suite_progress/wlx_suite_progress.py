"""What a suite had done when `tools/mutate.py` stopped waiting for it (XC-275), which tests
reached which lines (the map), and which tests a run was asked to keep (a selection).

A pytest plugin, loaded only by `mutate._run_suite` (`-p wlx_suite_progress`, with this
directory on the child's `PYTHONPATH` and nobody else's, so nothing in `tools/` can shadow
an import). It appends one JSON line to the file named by `WLX_SUITE_PROGRESS` for each
test that starts, each test that fails (in any phase), and each test that finishes.

**Why a file rather than pytest's output.** A mutant whose suite runs past the time
limit is killed, and pytest's short summary -- the only part of its output
`mutate._failures` trusts -- is printed at the end, so for a killed suite it never
exists. Until 2026-10-10 the harness counted such a mutant caught with nothing named,
whether or not any test had noticed it (`mutate.SUITE_TIMEOUT_SECONDS` says what that
hid). These lines are written as each test runs, through pytest's own hooks, so they
survive the kill and say which tests had failed and which one was still running.

**The map (CI-time design §4.1).** When the suite runs under coverage.py
(`mutate.py --build-map`, with `coverage.ini` beside this file), each test's lines --
setup, call and teardown -- are filed under its pytest node id, parametrization
included: `pytest_runtest_logstart` switches coverage's dynamic context to the id and
`pytest_runtest_logfinish` switches it back to `""`, where import and collection fall.
A child Python the test starts is measured too and files its lines under the same id,
through `WLX_COV_CONTEXT` (see `_measure_children_by_their_own_context`). `mutate.py`
then keeps only labels that exactly equal a node id this file recorded starting: any
other label (`""`, a `|`-joined one from a coverage run nested in another, a child that
lost its environment) is counted and printed but selects nothing, because nothing on the
mutant's side could run it as a test.

**A selection (§4.3).** With `WLX_SUITE_SELECT` naming a JSON list of node ids, only those
tests are kept after collection; the rest are reported deselected, and one `selected`
line says how many ids were wanted and how many were found. The ids come by file, never
argv: one parametrized id reached 65 KB (XC-331).

**Inert without its variables.** With `WLX_SUITE_PROGRESS` unset nothing is written and
nothing is opened; without coverage running no context is switched and no variable set;
without `WLX_SUITE_SELECT` nothing is deselected. Loading this anywhere else changes
nothing.
"""

from __future__ import annotations

import json
import os
import sys
import time

#: The environment variable naming the file to append to. `mutate.py` reads the name
#: from here rather than spelling it twice.
ENV = "WLX_SUITE_PROGRESS"

#: The running test's node id, while coverage measures the suite. `coverage.ini`'s
#: `context = ${WLX_COV_CONTEXT-}` reads it, so a child Python started during the test
#: files its lines under the test.
CONTEXT_ENV = "WLX_COV_CONTEXT"

#: The variable naming a JSON list of the node ids to keep.
SELECT_ENV = "WLX_SUITE_SELECT"


def _write(record: dict) -> None:
    path = os.environ.get(ENV)
    if not path:
        return
    # Opened and closed per line, so a line is on disk the moment it is written and
    # a kill can lose at most the line being written.
    with open(path, "a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


def _coverage():
    """The running `coverage.Coverage`, or `None` when coverage is not measuring.

    Read from `sys.modules` rather than imported: a suite not under coverage never has it
    loaded on its account. `Coverage.current()` is the last *started* instance (its
    `_instances` stack is pushed in `start()` and popped in `stop()`; coverage 7.17.4,
    `control.py:126-140` and `720-726`, read 2026-11-21; "version-added: 5.0"), so an
    instance a test merely constructed is not mistaken for the measuring one.
    """
    module = sys.modules.get("coverage")
    if module is None:
        return None
    return module.Coverage.current()


def _measure_children_by_their_own_context(cov) -> None:
    """Make a child Python re-read the rcfile, so it fills in `${WLX_COV_CONTEXT-}` itself.

    **This relies on one coverage internal, and `tests/test_mutate.py`'s
    `test_a_map_records_a_test_whose_child_process_reaches_the_function` is its guard.**
    `patch = subprocess` hands children the parent's config through
    `COVERAGE_PROCESS_CONFIG`, serialized once when the parent started, with the context
    already substituted (`patch.py:_patch_subprocess`, line 118; `config.py:111` and
    `serialize`, line 592), so every child would file its lines under the parent's `""`.
    A child's start-up prefers that variable to `COVERAGE_PROCESS_START`
    (`control.py:process_startup`, lines 1490-1497) and only deserializes it
    (`control.py:319-320`); all of this read in coverage 7.17.4 on 2026-11-21, and seen in
    an experiment that day, the child's lines landing under `""`. So this removes
    `COVERAGE_PROCESS_CONFIG` and sets `COVERAGE_PROCESS_START` to the rcfile the parent
    read (`config.config_file`, made absolute at `config.py:383`). That variable is
    coverage's documented way to measure a subprocess -- "The environment variable's value
    will be used as the name of the configuration file to use"
    (coverage.readthedocs.io/en/7.17.4/subprocess.html, read 2026-11-21) -- and a config
    file is read with the child's own environment. The rcfile keeps `patch = subprocess`,
    so the child sets `COVERAGE_PROCESS_CONFIG` again for its own children, frozen at the
    child's context: the same test id.

    Only when `patch = subprocess` put the variable there: a suite measured some other way
    keeps whatever it set up. If a coverage upgrade changes this, the guard test fails;
    a child that went unmapped would only cost speed, never a verdict.
    """
    if os.environ.pop("COVERAGE_PROCESS_CONFIG", None) is not None:
        os.environ["COVERAGE_PROCESS_START"] = cov.config.config_file


def pytest_collection_modifyitems(session, config, items):
    path = os.environ.get(SELECT_ENV)
    if not path:
        return
    with open(path, encoding="utf-8") as listed:
        wanted = list(dict.fromkeys(json.load(listed)))
    keep = set(wanted)
    kept = [item for item in items if item.nodeid in keep]
    dropped = [item for item in items if item.nodeid not in keep]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
    _write({"event": "selected", "wanted": len(wanted), "found": len(kept)})


def pytest_runtest_logstart(nodeid, location):
    _write({"event": "start", "nodeid": nodeid, "at": time.time()})
    cov = _coverage()
    if cov is not None:
        _measure_children_by_their_own_context(cov)
        # `switch_context` flushes what was collected under the previous label first, and
        # joins a non-empty static context as `static|new` (coverage 7.17.4,
        # `collector.py:374-383`, read 2026-11-21) -- which is why `mutate._run_suite`
        # starts every suite with `WLX_COV_CONTEXT` removed.
        cov.switch_context(nodeid)
        os.environ[CONTEXT_ENV] = nodeid


def pytest_runtest_logreport(report):
    if report.failed:
        _write({"event": "failed", "nodeid": report.nodeid, "when": report.when})
    if report.when == "teardown":
        # `at` too, so `mutate.build_map` can keep each test's own time, start to
        # teardown, and give a mutant's selected run a limit scaled to its tests.
        _write({"event": "finished", "nodeid": report.nodeid, "at": time.time()})


def pytest_runtest_logfinish(nodeid, location):
    cov = _coverage()
    if cov is not None:
        cov.switch_context("")
        os.environ.pop(CONTEXT_ENV, None)
