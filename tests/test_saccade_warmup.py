"""**An answer the settings let come inside the saccade detector's warm-up** (XC-505; the PI,
2026-11-14, asked in the UI: "Warn when settings allow it"): at load and on a live change, a warning
in every session kind, `answer-inside-saccade-warmup`, wherever a task's values let a state that
waits on a saccade begin before `saccade.Detector` has its `Params.warmup_samples` velocities, one
a frame. Nothing else changes: the detector stays per trial (S5 §5; `gaze.py`'s docstring).

Found when the pop-out with `inter_trial` at 0 and a 0.05 s `fix_hold` scored every scripted answer
`abort`: its answer's state, `flight`, began inside the warm-up, so the saccade was never seen and
the state ended on its landing bound. At the pop-out's starts (1.0 s between trials) the warm-up is
over before the fixation point appears.

The pop-out is read by path from the wl-xtasks checkout, as `tests/test_popout_search.py` reads it
(its helpers are used here: its scripted eye, `_Eye`, and the rig's gaze world under it, `_Seen`).
Engine test material: no timing claim rests on the frames counted here, which are the simulated
frame clock's.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import test_popout_search as popout
from wl_xcon import service
from wl_xcon.check import (
    ANSWER_INSIDE_WARMUP, FRAME_PERIOD, LAYOUT, answers_inside_warmup, check_task, laid_out,
)
from wl_xcon.cli import _load_allocation
from wl_xcon.findings import SESSION_KINDS
from wl_xcon.link import SetParameter, Simulated, Stop
from wl_xcon.run import Recorded, run_trial
from wl_xcon.saccade import Params
from wl_xcon.task import (
    After, Entered, Exited, Hold, On, Outcome, P, Param, SaccadeTo, State, Task, Trial, Window,
)
from wl_xcon.welfare import Deployment

needs_popout = pytest.mark.skipif(
    popout.XTASKS is None,
    reason="no wl-xtasks checkout holding tasks/popout_search.py; the pop-out is read from there",
)

FRAME, JAKE = popout.FRAME, popout.JAKE
#: The case that found XC-505: no time between trials, and a 0.05 s fixation hold.
QUICK = {"inter_trial": 0.0, "fix_hold": 0.05}


def _warned(findings) -> list:
    return [finding for finding in findings if finding.code == ANSWER_INSIDE_WARMUP]


def _started(task: Task, starts: dict, *, undrawn: tuple = ()) -> Task:
    """`task` with each of `starts` its setting's start, in every structure, and the task's own
    draws of `undrawn` gone, so the start is what a trial holds."""
    def started(param: Param) -> Param:
        return replace(param, start=starts[param.name]) if param.name in starts else param

    return replace(
        task,
        structures={name: replace(structure, params=[started(p) for p in structure.params])
                    for name, structure in task.structures.items()},
        draws={name: draw for name, draw in task.draws.items() if name not in undrawn})


def _load(task: Task) -> list:
    return check_task(task, _load_allocation(Path(popout.ALLOCATION)), geometry=popout.DIRECT,
                      calibration=popout.SRGB, deployment=Deployment.RIG_FIXED)


@pytest.fixture(scope="module")
def task():
    return popout._load_task(popout.TASK_FILE)


@needs_popout
def test_the_popout_at_its_starts_does_not_warn(task):
    """At the PI's starts the warm-up is over long before an answer can come: a second between
    trials, then the fixation point found, held at least 0.75 s (its draw's least), the go and the
    eye leaving it. Not at load, and not at the starts set."""
    assert _warned(_load(task)) == []
    starts = {param.name: param.start for param in task.params if param.start is not None}
    assert _warned(laid_out(task, starts)) == []


@needs_popout
def test_no_time_between_trials_and_a_short_hold_warns_at_load(task):
    """**The case that found XC-505**, as a task's own starts: `inter_trial` 0 and `fix_hold` a
    fixed 0.05 s. `flight`, the answer's state, can begin 15 frames into a trial -- one each for the
    blank's zero wait, the fixation point found and the eye leaving it, 12 for the hold, none for
    the zero `go_delay` (TSK-78) -- 0.0625 s at 240 Hz, inside the detector's 50 frames: **a warning
    in every kind, never a refusal**, said once for both structures that search, naming the
    settings that put it there. The catches, which wait on no saccade, say nothing. **And it is
    what the trial loop does**: the rig's gaze world under a scripted eye that leaves for the odd
    one at the go enters `flight` inside the warm-up and scores the answer `abort`; with a second
    between trials, `correct`."""
    quick = _started(task, QUICK, undrawn=("fix_hold",))

    (finding,) = _warned(_load(quick))

    assert not finding.blocking and finding.accepted_in == SESSION_KINDS
    assert finding.detail.startswith("in structures 'search', 'odd_one_alone': "), finding.detail
    for said in ("an answer can come 0.0625 s into a trial, in state 'flight'",
                 "`inter_trial` 0 s, `fix_hold` 0.05 s, `go_delay` 0 s",
                 "blind for each trial's first 0.208 s", "50 frames at 240 Hz",
                 "scored as the task's error", "by 0.146 s or more"):
        assert said in finding.detail, (said, finding.detail)
    assert finding.subjects == ("search: state 'flight'", "odd_one_alone: state 'flight'")

    def played(values: dict):
        bound = {**popout._values(task), **values}
        eye = popout._Eye([("go", 0, popout.ODD_ONE)], start=popout.FIXATION)
        result = run_trial(task.trial, popout._Seen(eye, task.trial, bound), FRAME,
                           values=dict(bound), effects=Recorded())
        return result.outcome, eye.begun["flight"]

    outcome, flight = played(QUICK)
    assert outcome is Outcome.ABORT and flight <= Params().warmup_samples
    outcome, flight = played({**QUICK, "inter_trial": 1.0})
    assert outcome is Outcome.CORRECT and flight > Params().warmup_samples


@needs_popout
def test_the_warning_follows_the_values_set(task):
    """At the values set (`laid_out`, the run's pre-flight and each live change's): into the
    region and out of it. No time between trials alone leaves the drawn hold's 0.75 s; a fixed
    0.05 s hold with it warns, and so does a drawn one whose least is 0.05 s, said "at its least";
    a 0.5 s hold, or a second between trials, clears it."""
    assert _warned(laid_out(task, {"inter_trial": 0.0})) == []
    (fixed,) = _warned(laid_out(task, QUICK))
    assert "`fix_hold` 0.05 s," in fixed.detail
    (drawn,) = _warned(laid_out(task, {"inter_trial": 0.0, "fix_hold": (0.05, 0.5)}))
    assert "`fix_hold` 0.05 s at its least" in drawn.detail
    assert _warned(laid_out(task, {**QUICK, "fix_hold": 0.5})) == []
    assert _warned(laid_out(task, {**QUICK, "inter_trial": 1.0})) == []


class _Changes(Simulated):
    """**A trainer who makes `changes` in turn**, one every `EVERY` drains from the first, and
    stops the run `EVERY` drains after the last."""

    EVERY = 2

    def __init__(self, changes: list) -> None:
        super().__init__()
        self.changes, self.drains = changes, 0

    def drain(self) -> list:
        self.drains += 1
        sent, due = divmod(self.drains - 1, self.EVERY)
        if due == 0 and sent < len(self.changes):
            self.queue(SetParameter(*self.changes[sent], JAKE))
        if self.drains == self.EVERY * (len(self.changes) + 1):
            self.queue(Stop(by=JAKE))
        return super().drain()


@needs_popout
def test_a_live_change_into_the_region_warns_and_one_out_of_it_clears_it(tmp_path):
    """**On a live change** (`taskd.Session._laid_out`): no time between trials warns of nothing;
    the hold set to 0.05 s then puts the answer inside the warm-up, a warning the trainer accepts
    by making the change (`warnings.jsonl`, `how` `set`), said on the changes feed, once; the hold
    set to 0.5 s takes it out again, adding nothing, and the layout at the values the run ends on
    holds no such warning. Each value applies: a warning, never a refusal."""
    trainer = _Changes([("inter_trial", 0.0), ("fix_hold", 0.05), ("fix_hold", 0.5)])
    session = popout._opened(popout._spec(tmp_path, seed=5, start_block="Fade-in"), trainer)

    session.run()

    assert session.refusals == []
    rows = [row for row in popout._rows(session, "warnings.jsonl")
            if row["code"] == ANSWER_INSIDE_WARMUP]
    assert len(rows) == 1, rows
    assert rows[0]["how"] == "set" and "`fix_hold` 0.05 s," in rows[0]["detail"], rows[0]
    fed = [said for kind, _, _, said in session.controls
           if kind == "warning" and said.startswith(ANSWER_INSIDE_WARMUP)]
    assert fed == [f"{ANSWER_INSIDE_WARMUP}: {rows[0]['detail']}"]
    lines = popout._rows(session, "trials.jsonl")
    assert {0.05, 0.5} <= {line["params"]["fix_hold"] for line in lines}
    last = lines[-1]["params"]
    assert (last["inter_trial"], last["fix_hold"]) == (0.0, 0.5)
    assert _warned(session._layouts(dict(session.spec.values), set(session._edited), {}, {})) == []


def _bare(states: list, start: str, params: list) -> Task:
    trial = Trial(start=start, view="direct", params=params,
                  windows=[Window("fix", at=(0.0, 0.0), radius=2.0),
                           Window("far", at=(8.0, 0.0), radius=2.0)],
                  states=states)
    return Task({"t": trial}, default="t")


#: Every wait at zero, so a trial can end on its third frame.
ZERO = [Param("wait", unit="s", low=0.0, start=0.0), Param("hold", unit="s", low=0.0, start=0.0)]


def test_a_task_with_no_saccade_decided_state_never_warns(task):
    """A state the detector does not decide -- a gaze edge, a hold, a clock -- is judged by window
    membership from a trial's first frame, so however soon it begins nothing is warned: a bare
    task whose every wait is 0, at load and at the values set; and the pop-out's catches, whose
    answer is a hold on the fixation point, at the very values that warn its searches."""
    states = [
        State("between", go=[On(After(P("wait")), "look")]),
        State("look", go=[On(Entered("fix"), "hold"), On(After(1.0), Outcome.NO_FIXATION)]),
        State("hold", go=[On(Hold("fix", P("hold")), Outcome.CORRECT),
                          On(Exited("fix"), Outcome.FIXATION_BREAK),
                          On(After(1.0), Outcome.NO_RESPONSE)]),
    ]
    bare = _bare(states, "between", ZERO)
    assert _warned(check_task(bare)) == []
    assert _warned(laid_out(bare, {"wait": 0.0, "hold": 0.0})) == []
    if popout.XTASKS is not None:
        catches = replace(task, structures={
            name: task.structures[name]
            for name in ("target_absent", "odd_place_empty", "distractor_alone")},
            default="target_absent", specials=())
        assert _warned(answers_inside_warmup(catches, QUICK)) == []


def test_the_warmup_is_counted_in_the_rigs_frames():
    """**The detector's warm-up is frames, so its length is the frame rate's**: a state waiting on
    a saccade 0.5 s into a trial is 120 frames in at 240 Hz, past the 50 of the warm-up, and 30
    frames in at 60 Hz, inside it (0.833 s there), said in that rate. Where no caller names a frame
    period, the check counts the one every session here runs at (`service.FRAME_PERIOD`). And the
    warning is one of the checks judged at the values set (`check.LAYOUT`)."""
    states = [
        State("between", go=[On(After(P("wait")), "answer")]),
        State("answer", go=[On(SaccadeTo("far"), Outcome.CORRECT),
                            On(After(1.0), Outcome.NO_RESPONSE)]),
    ]
    task = _bare(states, "between", [Param("wait", unit="s", low=0.0, start=0.5)])

    assert FRAME_PERIOD == service.FRAME_PERIOD
    assert _warned(check_task(task)) == []
    assert _warned(answers_inside_warmup(task, {}, 1 / 240)) == []
    (slow,) = _warned(answers_inside_warmup(task, {}, 1 / 60))
    assert "0.5 s into a trial, in state 'answer'" in slow.detail
    assert "first 0.833 s (its warm-up, 50 frames at 60 Hz)" in slow.detail
    (live,) = _warned(laid_out(task, {"wait": 0.1}))
    assert "`wait` 0.1 s" in live.detail
    assert ANSWER_INSIDE_WARMUP in LAYOUT


@needs_popout
def test_a_live_change_is_judged_at_the_sessions_own_frame_rate(tmp_path):
    """**Live, the session's own frame period** (`SessionSpec.frame_period`, which
    `taskd.Session._layouts` passes): no time between trials leaves the drawn hold's least, 0.75 s,
    which at 240 Hz puts the answer 182 frames in, past the warm-up, and at 60 Hz 48 frames in,
    inside its 50 (0.833 s there): warned on a 60 Hz session, said in its rate."""
    trainer = _Changes([("inter_trial", 0.0)])
    spec = replace(popout._spec(tmp_path, seed=5, start_block="Fade-in"), frame_period=1 / 60)
    session = popout._opened(spec, trainer)

    session.run()

    assert session.refusals == []
    (row,) = [row for row in popout._rows(session, "warnings.jsonl")
              if row["code"] == ANSWER_INSIDE_WARMUP]
    assert "0.8 s into a trial" in row["detail"] and "50 frames at 60 Hz" in row["detail"], row
