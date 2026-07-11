"""Scheduler ``--loop`` path regression test.

WHY THIS FILE EXISTS
--------------------
The ``--loop`` daemon path (``python -m runtime.main --loop``) was the single
largest untested blind spot in the system. Verified against source (AST + read):

  * ``main.py`` ``--loop`` instantiates
    ``Scheduler(orchestrator=orchestrator, interval_minutes=interval)`` and then
    calls ``scheduler.run_loop()`` (main.py:123 / :134).  The graceful-shutdown
    glue is ``signal.signal(SIGTERM/SIGINT, ...) -> scheduler.stop()``
    (main.py:126-131).
  * ``Scheduler.run_loop`` (runtime/scheduler.py:44) is a *synchronous*
    ``while self._running: run_once(); time.sleep(self.interval_seconds)`` loop.
    The time-block point is ``time.sleep`` imported at module top, so the only
    safe patch target is ``runtime.scheduler.time.sleep``.
  * There is NO max-iteration / watchdog / timeout guard. The loop only exits
    when ``stop()`` flips ``self._running`` to False.  ``run_loop`` itself has
    no try/except, so an exception raised inside ``run_once`` (i.e. inside
    ``Orchestrator.run_pipeline``) propagates straight out of ``run_loop`` — a
    single bad cycle kills the whole 7x24 daemon.  That fragility is asserted
    here as a REGRESSION GUARD (and flagged as a risk to harden), NOT changed.

THE HARD PART — avoiding a real (or infinite) sleep in the test:
  ``run_loop`` would otherwise block for ``interval_seconds`` (>=900s at the
  default 15 min) and, with no stop trigger, loop forever.  We patch
  ``runtime.scheduler.time.sleep`` so that the FIRST sleep triggers
  ``scheduler.stop()`` (simulating the signal-driven graceful shutdown), which
  flips ``_running`` and lets the loop exit after exactly one cycle.  This
  exercises the REAL stop mechanism instead of faking it.

No source code is modified. Pure additive.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from runtime.scheduler import Scheduler


def _make_orchestrator(run_pipeline_return=None, side_effect=None):
    orch = MagicMock()
    if side_effect is not None:
        orch.run_pipeline.side_effect = side_effect
    else:
        orch.run_pipeline.return_value = run_pipeline_return
    return orch


def _fake_decision():
    """Mirror the attributes ``run_once`` reads on a real decision object."""
    return SimpleNamespace(action=SimpleNamespace(value="HOLD"), confidence=0.5)


class TestSchedulerRunOnce:
    """Isolated single-cycle behavior (no time.sleep involved)."""

    def test_run_once_calls_run_pipeline(self):
        orch = _make_orchestrator(run_pipeline_return=None)
        sched = Scheduler(orch, interval_minutes=15)
        sched.run_once()
        orch.run_pipeline.assert_called_once()

    def test_run_once_none_decision_does_not_raise(self):
        orch = _make_orchestrator(run_pipeline_return=None)
        sched = Scheduler(orch, interval_minutes=15)
        sched.run_once()  # must not raise when pipeline yields no decision
        orch.run_pipeline.assert_called_once()

    def test_run_once_reads_action_and_confidence(self):
        orch = _make_orchestrator(run_pipeline_return=_fake_decision())
        sched = Scheduler(orch, interval_minutes=15)
        sched.run_once()  # accesses decision.action.value + decision.confidence
        orch.run_pipeline.assert_called_once()


class TestSchedulerLoop:
    """The 7x24 loop path — terminated safely via the REAL stop() mechanism."""

    def test_run_loop_runs_exactly_one_cycle_then_stops(self):
        orch = _make_orchestrator(run_pipeline_return=None)
        sched = Scheduler(orch, interval_minutes=0)  # interval_seconds = 0

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            sched.stop()  # simulate signal-driven graceful shutdown

        with patch("runtime.scheduler.time.sleep", side_effect=fake_sleep):
            sched.run_loop()

        # exactly one cycle executed, then the sleep triggered stop()
        assert orch.run_pipeline.call_count == 1, "run_loop 未执行恰好一轮"
        assert sleep_calls == [0], "time.sleep 未被以 interval_seconds=0 调用"
        assert sched._running is False, "stop() 未将 _running 置 False"

    def test_run_loop_sleeps_for_interval_seconds(self):
        orch = _make_orchestrator(run_pipeline_return=None)
        sched = Scheduler(orch, interval_minutes=2)  # interval_seconds = 120

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            sched.stop()

        with patch("runtime.scheduler.time.sleep", side_effect=fake_sleep):
            sched.run_loop()

        assert sleep_calls == [120], "循环未按 interval_minutes*60 秒阻塞"

    def test_run_loop_propagates_pipeline_exception(self):
        """REGRESSION GUARD: a single bad cycle kills the daemon.

        ``run_loop`` has no internal try/except, so an exception raised inside
        ``run_once`` (i.e. ``Orchestrator.run_pipeline``) propagates straight
        out of ``run_loop``.  This is the fragile behavior we must NOT silently
        paper over — it is asserted so any future hardening (e.g. a per-cycle
        try/except) is a deliberate, visible change rather than an accident.
        """
        orch = _make_orchestrator(side_effect=RuntimeError("pipeline boom"))

        def fake_sleep(seconds):  # never reached; exception precedes the sleep
            sched.stop()

        sched = Scheduler(orch, interval_minutes=0)
        with patch("runtime.scheduler.time.sleep", side_effect=fake_sleep):
            try:
                sched.run_loop()
            except RuntimeError as exc:
                assert str(exc) == "pipeline boom"
            else:
                raise AssertionError(
                    "run_loop 未将 pipeline 异常向上传播 — 守护进程脆点已被静默"
                )


class TestSchedulerStopHealth:
    def test_health_reports_interval_seconds(self):
        sched = Scheduler(MagicMock(), interval_minutes=10)
        assert sched.health() == {"running": False, "interval_seconds": 600}

    def test_stop_flips_running_flag_to_false(self):
        sched = Scheduler(MagicMock(), interval_minutes=5)
        sched._running = True
        sched.stop()
        assert sched._running is False
        assert sched.health()["running"] is False
