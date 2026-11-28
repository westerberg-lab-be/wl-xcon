"""**An answer the settings let come before the saccade detector is ready** (XC-505; the PI,
2026-11-14, asked in the UI: "Warn when settings allow it"): at load and on a live change, a warning
in every session kind, `answer-inside-saccade-warmup`, wherever a task's values let a state that
waits on a saccade begin before `saccade.Detector` is ready -- its `Params.warmup_samples`
velocities, one a frame from the trial's fifth sample (`check.blind_frames`). Nothing else changes:
the detector stays per trial (S5 §5; `gaze.py`'s docstring).

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

import random
from dataclasses import replace
from pathlib import Path

import pytest

import test_popout_search as popout
from wl_xcon import preflight, service
from wl_xcon.check import (
    ANSWER_INSIDE_WARMUP, FRAME_PERIOD, LAYOUT, _entries, _sources, answers_inside_warmup,
    blind_frames, check_task, laid_out,
)
from wl_xcon.cli import _load_allocation
from wl_xcon.findings import SESSION_KINDS
from wl_xcon.link import SetParameter, Simulated, Stop
from wl_xcon.run import Recorded, run_trial
from wl_xcon.saccade import Detector
from wl_xcon.task import (
    After, Block, Condition, Entered, Exited, Hold, On, Onscreen, Outcome, P, Param, Plan,
    SaccadeOnset, SaccadeTo, State, Task, Trial, Window,
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


def test_the_detector_is_ready_on_the_frame_the_check_counts():
    """**`check.blind_frames` is the detector's own**: fed one sample a frame, `saccade.Detector`
    is first ready on sample 54 -- its first velocity on the fifth, its 50th on the 54th -- and
    not one sample sooner."""
    detector, noise = Detector(), random.Random(1)
    ready = next(sample for sample in range(1, 200)
                 if detector.accept(sample * FRAME, (noise.gauss(0.0, 0.05),
                                                     noise.gauss(0.0, 0.05))) is None
                 and detector.ready)
    assert ready == blind_frames() == 54


@needs_popout
def test_the_rigs_gaze_world_is_ready_on_that_frame_of_a_trial(task):
    """And the rig's gaze world (`gaze.Tracked`) feeds its detector one sample a frame from a
    trial's first: its detector is first ready on frame `blind_frames()`."""
    bound = popout._values(task)
    eye = popout._Eye([], start=popout.FIXATION)
    first: list[int] = []

    class Seen(popout._Seen):
        def display(self, visible, frame: int) -> None:
            popout._Seen.display(self, visible, frame)
            if self.detector.ready and not first:
                first.append(frame)

    run_trial(task.trial, Seen(eye, task.trial, bound), FRAME, values=dict(bound),
              effects=Recorded(), max_frames=120)
    assert first == [blind_frames()]


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
    the zero `go_delay` (TSK-78) -- 0.0625 s at 240 Hz, before the detector is ready on frame 54:
    **a warning in every kind, never a refusal**, said once for both structures that search,
    naming the settings that put it there, and nowhere else since it holds wherever the task can
    run. The catches, which wait on no saccade, say nothing. **And it is what the trial loop
    does**: the rig's gaze world under a scripted eye that leaves for the odd one at the go enters
    `flight` inside the warm-up and scores the answer `abort`; with a second between trials,
    `correct`."""
    quick = _started(task, QUICK, undrawn=("fix_hold",))

    (finding,) = _warned(_load(quick))

    assert not finding.blocking and finding.accepted_in == SESSION_KINDS
    assert finding.detail.startswith("in structures 'search', 'odd_one_alone': "), finding.detail
    for said in ("an answer can come 0.0625 s into a trial, in state 'flight'",
                 "`inter_trial` 0 s, `fix_hold` 0.05 s, `go_delay` 0 s",
                 "not ready until frame 54 of each trial, 0.225 s at 240 Hz",
                 "scored as the task's error", "by 0.167 s or more in all"):
        assert said in finding.detail, (said, finding.detail)
    assert finding.detail.endswith("in all"), finding.detail
    assert finding.subjects == ("search: state 'flight'", "odd_one_alone: state 'flight'")

    def played(values: dict):
        bound = {**popout._values(task), **values}
        eye = popout._Eye([("go", 0, popout.ODD_ONE)], start=popout.FIXATION)
        result = run_trial(task.trial, popout._Seen(eye, task.trial, bound), FRAME,
                           values=dict(bound), effects=Recorded())
        return result.outcome, eye.begun["flight"]

    outcome, flight = played(QUICK)
    assert outcome is Outcome.ABORT and flight <= blind_frames()
    outcome, flight = played({**QUICK, "inter_trial": 1.0})
    assert outcome is Outcome.CORRECT and flight > blind_frames()


@needs_popout
def test_a_plan_that_sets_the_quick_values_warns_at_load_naming_where(task):
    """**The review's I1: a plan is judged at load**, as `little-time-to-answer` is (`wlx check`
    and the open's checks): the pop-out at its own starts with a plan whose one condition sets the
    quick values is warned, naming that plan's block and condition, and only it -- the plain run,
    at the starts, is not named."""
    quick = Plan([Block("Quick", [Condition("quick", dict(QUICK), target=1)])])
    planned = replace(task, plans={**task.plans, "quick": quick})

    (finding,) = _warned(_load(planned))

    assert "0.0625 s into a trial, in state 'flight'" in finding.detail, finding.detail
    assert finding.detail.endswith(" (in plan 'quick' (Quick: quick))"), finding.detail
    assert _warned(_load(task)) == []


@needs_popout
def test_the_warning_follows_the_values_set(task):
    """At the values set (`laid_out`, the run's pre-flight and each live change's): into the
    region and out of it. No time between trials alone leaves the drawn hold's 0.75 s; a fixed
    0.05 s hold with it warns, and so does a drawn one whose least is 0.05 s, said "at its least";
    a 0.5 s hold, or a second between trials, takes it out."""
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
def test_a_live_change_into_the_region_warns_once_and_the_layout_out_of_it_holds_none(tmp_path):
    """**On a live change** (`taskd.Session._laid_out`): no time between trials warns of nothing;
    the hold set to 0.05 s then puts the answer inside the warm-up, a warning the trainer accepts
    by making the change (`warnings.jsonl`, `how` `set`), said on the changes feed, once; the hold
    set to 0.5 s takes it out again, adding nothing, and the layout at the values the run ends on
    holds no such warning. (An accepted warning stays on the session's list, as every warning
    does: engine spec §19.2.) Each value applies: a warning, never a refusal."""
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


@needs_popout
def test_a_live_change_is_judged_at_the_sessions_own_frame_rate(tmp_path):
    """**Live, the session's own frame period** (`SessionSpec.frame_period`, which
    `taskd.Session._layouts` passes): no time between trials leaves the drawn hold's least, 0.75 s,
    which at 240 Hz puts the answer 182 frames in, past the warm-up, and at 60 Hz 48 frames in,
    before frame 54 (0.9 s there): warned on a 60 Hz session, said in its rate."""
    trainer = _Changes([("inter_trial", 0.0)])
    spec = replace(popout._spec(tmp_path, seed=5, start_block="Fade-in"), frame_period=1 / 60)
    session = popout._opened(spec, trainer)

    session.run()

    assert session.refusals == []
    (row,) = [row for row in popout._rows(session, "warnings.jsonl")
              if row["code"] == ANSWER_INSIDE_WARMUP]
    assert "0.8 s into a trial" in row["detail"] and "0.9 s at 60 Hz" in row["detail"], row


#: A task file whose answer state is waited on 0.5 s into a trial: 120 frames at 240 Hz, 30 at 60.
HALF_SECOND = """
from wl_xcon.task import After, On, Outcome, P, Param, SaccadeTo, State, Task, Trial, Window

TASK = Task({"t": Trial(
    start="between", view="direct",
    params=[Param("wait", unit="s", low=0.0, start=0.5)],
    windows=[Window("far", at=(8.0, 0.0), radius=2.0)],
    states=[State("between", go=[On(After(P("wait")), "answer")]),
            State("answer", go=[On(SaccadeTo("far"), Outcome.CORRECT),
                                On(After(1.0), Outcome.NO_RESPONSE)])])}, default="t")
"""


def test_a_runs_preflight_is_judged_at_the_sessions_own_frame_rate(tmp_path):
    """**And a run's pre-flight** (the review's M3): `preflight.task` and `preflight.laid_out`
    count the session's frames (`Service._preflight` passes `SessionSpec.frame_period`): an
    answer 0.5 s into a trial is warned at 60 Hz and not at 240, by both."""
    path = tmp_path / "half_second.py"
    path.write_text(HALF_SECOND)
    allocation = _load_allocation(Path(popout.ALLOCATION))
    for period, warned in ((1 / 240, False), (1 / 60, True)):
        _, loaded, findings = preflight.task(path, allocation, popout.DIRECT, popout.SRGB,
                                             Deployment.RIG_FIXED, frame_period=period)
        assert bool(_warned(findings)) is warned, (period, findings)
        laid = preflight.laid_out(loaded, {}, None, allocation, popout.DIRECT, popout.SRGB,
                                  Deployment.RIG_FIXED, frame_period=period)
        assert bool(_warned(laid)) is warned, (period, laid)


# --- Bare tasks: the rules, each pinned against the trial loop (the review's I2) ----------------


def _bare(states: list, start: str, params: list, **task) -> Task:
    trial = Trial(start=start, view="direct", params=params,
                  windows=[Window("fix", at=(0.0, 0.0), radius=2.0),
                           Window("far", at=(8.0, 0.0), radius=2.0)],
                  states=states)
    return Task({"t": trial}, default="t", **task)


def _seconds(name: str, start: float) -> Param:
    return Param(name, unit="s", low=0.0, start=start)


def _answer(*go) -> State:
    """The answer's state: `go`, and a bound."""
    return State("answer", go=[*go, On(After(1.0), Outcome.NO_RESPONSE)])


def _timed(frames: int) -> Task:
    """A start state's wait of `frames` frames, then a state waiting on a saccade."""
    return _bare([State("between", go=[On(After(P("wait")), "answer")]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))],
                 "between", [_seconds("wait", frames * FRAME)])


def _clocked(frames: int) -> Task:
    """A zero wait, then a clock from the photodiode of `frames` frames, then the answer: 1 + (1 +
    `frames`) frames to the answer's entry."""
    return _bare([State("between", go=[On(After(0.0), "cue")]),
                  State("cue", go=[On(After(P("delay"), since=Onscreen()), "answer"),
                                   On(After(1.0), Outcome.NO_FIXATION)]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))],
                 "between", [_seconds("delay", frames * FRAME)])


def _onset_only() -> Task:
    """A zero wait, then an answer waiting only on a saccade's onset."""
    return _bare([State("between", go=[On(After(0.0), "answer")]),
                  _answer(On(SaccadeOnset(), Outcome.CORRECT))], "between", [])


def _onset_clock() -> Task:
    """A zero wait, then an answer whose only saccade link is a clock its onset starts."""
    return _bare([State("between", go=[On(After(0.0), "answer")]),
                  _answer(On(After(0.1, since=SaccadeOnset()), Outcome.CORRECT))], "between", [])


def _hidden_by_a_pass() -> Task:
    """The review's M1: `cue`'s zero wait is its pass, taken on the frame it is entered, so its
    `Entered` is never judged; the answer comes through `late`, 0.5 s on."""
    return _bare([State("between", go=[On(After(0.0), "cue")]),
                  State("cue", go=[On(Entered("fix"), "answer"),
                                   On(After(P("cue_wait")), "late")]),
                  State("late", go=[On(After(0.5), "answer")]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))],
                 "between", [_seconds("cue_wait", 0.0)])


def _held() -> Task:
    """A zero wait, the fixation point entered, a 0.05 s hold, a zero pass, then the answer: the
    pop-out's path in small, 1 + 1 + 12 + 0 frames."""
    return _bare([State("between", go=[On(After(0.0), "look")]),
                  State("look", go=[On(Entered("fix"), "hold"),
                                    On(After(1.0), Outcome.NO_FIXATION)]),
                  State("hold", go=[On(Hold("fix", P("hold")), "delay"),
                                    On(Exited("fix"), Outcome.FIXATION_BREAK)]),
                  State("delay", go=[On(After(0.0), "answer")]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))],
                 "between", [_seconds("hold", 0.05)])


#: Each bare task, the frame its answer is entered on, and whether that is warned (before 54).
RULES = [
    ("a wait of 52 frames", _timed(52), 52, True),
    ("a wait of 53 frames, the last warned", _timed(53), 53, True),
    ("a wait of 54 frames, the detector ready as the answer is judged", _timed(54), 54, False),
    ("a clock from the photodiode, a frame after it", _clocked(51), 53, True),
    ("a clock a frame longer", _clocked(52), 54, False),
    ("an answer on a saccade's onset alone", _onset_only(), 1, True),
    ("an answer on a clock a saccade's onset starts", _onset_clock(), 1, True),
    ("a pass that hides its state's other edges", _hidden_by_a_pass(), 121, False),
    ("the pop-out's path in small", _held(), 14, True),
]


@pytest.mark.parametrize(("what", "bare", "entered", "warned"), RULES, ids=[r[0] for r in RULES])
def test_each_rule_of_the_count_warns_where_it_should(what, bare, entered, warned):
    """The review's I2: the boundary (the answer entered on frame 53 is warned, on 54 not), a clock
    timed from another guard (a frame after it), a state waiting on a saccade's onset alone or on
    a clock it starts, and a pass that leaves its state at once (the review's M1), each at load and
    at the values set, the sentence naming when the answer can come."""
    loaded, live = _warned(check_task(bare)), _warned(laid_out(bare, {}))
    assert bool(loaded) is warned and bool(live) is warned, (what, loaded, live)
    if warned:
        assert f"{entered * FRAME:.3g} s into a trial, in state 'answer'" in loaded[0].detail


class _Gazing:
    """**A world whose eye is in every window and whose photodiode always reports**, telling
    each state's first frame: every gaze edge fires on a state's first judged frame, as the
    check counts it, and no saccade is ever seen."""

    def __init__(self) -> None:
        self.begun: dict[str, int] = {}

    def in_window(self, window: str, frame: int, eye: str = "both") -> bool:
        return True

    def happened(self, guard, state: str, frame: int) -> bool:
        return isinstance(guard, Onscreen)

    def began(self, state: str, frame: int) -> None:
        self.begun.setdefault(state, frame)

    def signal(self, frame: int) -> str:
        return "ok"

    def display(self, visible, frame: int) -> None:
        return None


@pytest.mark.parametrize(("what", "bare", "entered", "warned"), RULES, ids=[r[0] for r in RULES])
def test_the_count_is_the_trial_loops(what, bare, entered, warned):
    """**The count is `run._Loop`'s, not a sentence's**: run through the trial loop with every gaze
    edge firing as soon as it can, each answer state begins (`World.began`) on the frame after the
    one the check says it is entered on, `_entries`' count."""
    trial = bare.structures["t"]
    starts = {param.name: param.start for param in bare.params}
    sources = _sources(bare, Block("session", [Condition("session", {}, target=1)]),
                       Condition("session", {}, target=1), starts, frozenset(), {},
                       {param.name: param for param in bare.params})
    world = _Gazing()
    run_trial(trial, world, FRAME, values=dict(starts), effects=Recorded())

    assert _entries(trial, sources, FRAME)["answer"][0] == entered
    assert world.begun["answer"] - 1 == entered, (what, world.begun)


def test_a_state_only_a_saccade_can_enter_is_not_warned():
    """**The review's M2**: what a detected edge enters can never be entered inside the warm-up, so
    of a saccade's onset leading to a state waiting on where one lands, only the first is warned."""
    bare = _bare([State("between", go=[On(After(0.0), "first")]),
                  State("first", go=[On(SaccadeOnset(), "answer"),
                                     On(After(1.0), Outcome.NO_RESPONSE)]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))], "between", [])
    (only,) = _warned(check_task(bare))
    assert "in state 'first'" in only.detail


def test_a_task_with_no_saccade_decided_state_never_warns():
    """A state the detector does not decide -- a gaze edge, a hold, a clock -- is judged by window
    membership from a trial's first frame, so however soon it begins nothing is warned: a bare
    task whose every wait is 0, at load and at the values set."""
    states = [
        State("between", go=[On(After(P("wait")), "look")]),
        State("look", go=[On(Entered("fix"), "hold"), On(After(1.0), Outcome.NO_FIXATION)]),
        State("hold", go=[On(Hold("fix", P("hold")), Outcome.CORRECT),
                          On(Exited("fix"), Outcome.FIXATION_BREAK),
                          On(After(1.0), Outcome.NO_RESPONSE)]),
    ]
    bare = _bare(states, "between", [_seconds("wait", 0.0), _seconds("hold", 0.0)])
    assert _warned(check_task(bare)) == []
    assert _warned(laid_out(bare, {"wait": 0.0, "hold": 0.0})) == []


@needs_popout
def test_the_popouts_catches_never_warn(task):
    """And the pop-out's catches, whose answer is a hold on the fixation point, at the very values
    that warn its searches."""
    catches = replace(task, structures={
        name: task.structures[name]
        for name in ("target_absent", "odd_place_empty", "distractor_alone")},
        default="target_absent", specials=())
    assert _warned(answers_inside_warmup(catches, QUICK)) == []


def test_the_warmup_is_counted_in_the_rigs_frames():
    """**The detector's warm-up is frames, so its length is the frame rate's**: a state waiting on
    a saccade 0.5 s into a trial is 120 frames in at 240 Hz, past the detector's 54, and 30 frames
    in at 60 Hz, before them (0.9 s there), said in that rate. Where no caller names a frame
    period, the check counts the one every session here runs at (`service.FRAME_PERIOD`). And the
    warning is one of the checks judged at the values set (`check.LAYOUT`)."""
    task = _bare([State("between", go=[On(After(P("wait")), "answer")]),
                  _answer(On(SaccadeTo("far"), Outcome.CORRECT))],
                 "between", [_seconds("wait", 0.5)])

    assert FRAME_PERIOD == service.FRAME_PERIOD
    assert _warned(check_task(task)) == []
    assert _warned(answers_inside_warmup(task, {}, 1 / 240)) == []
    (slow,) = _warned(answers_inside_warmup(task, None, 1 / 60))
    assert "0.5 s into a trial, in state 'answer'" in slow.detail
    assert "not ready until frame 54 of each trial, 0.9 s at 60 Hz" in slow.detail
    (live,) = _warned(laid_out(task, {"wait": 0.1}))
    assert "`wait` 0.1 s" in live.detail
    assert ANSWER_INSIDE_WARMUP in LAYOUT


def test_the_fix_it_says_is_enough():
    """**The review's M4**: lengthening a wait at its one-frame floor gains a frame less than it is
    lengthened by, so the fix said counts a frame more. A start state's zero wait before an
    answer, its fix 54 frames' worth (0.225 s): at that value the answer is no longer warned."""
    zero = _timed(0)
    (finding,) = _warned(check_task(zero))
    assert "by 0.225 s or more in all" in finding.detail, finding.detail
    assert _warned(laid_out(zero, {"wait": 0.225})) == []
