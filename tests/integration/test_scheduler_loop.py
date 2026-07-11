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
    when ``stop()`` flips ``self._running`` to False.  ``run_loop`` isolates
    each cycle in a ``try/except Exception``: a cycle that raises inside
    ``run_once`` (i.e. inside ``Orchestrator.run_pipeline``) is caught, logged,
    and reported via ``_notify_crash`` (a best-effort ``[CRITICAL_ALERT]`` to
    the Feishu adapter), then the loop sleeps and proceeds to the next cycle.
    A single bad cycle can NO LONGER kill the 7x24 daemon.  This contract is
    locked by ``test_run_loop_recovers_from_pipeline_exception_and_alerts``.

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

    def test_run_loop_recovers_from_pipeline_exception_and_alerts(self):
        """HARDENED CONTRACT: a bad cycle is isolated, alerted, and the loop
        proceeds to the next cycle instead of dying.

        Replaces the old regression guard (exception propagated out of
        run_loop).  The previous fragility — a single failed cycle killing the
        entire 7x24 daemon — was explicitly hardened; this test now locks the
        NEW behavior: catch + log + ``[CRITICAL_ALERT]`` + continue.  Any future
        regression back to "one bad cycle kills the daemon" will fail here.
        """
        orch = _make_orchestrator(side_effect=RuntimeError("pipeline boom"))

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            sched.stop()  # signal-driven graceful shutdown still ends the loop

        sched = Scheduler(orch, interval_minutes=0)
        with patch("runtime.scheduler.time.sleep", side_effect=fake_sleep):
            sched.run_loop()  # must NOT raise — the daemon survives the bad cycle

        # pipeline ran once, crashed, and was caught (no propagation upward)
        assert orch.run_pipeline.call_count == 1
        # crash alert dispatched with the required [CRITICAL_ALERT] token
        assert orch.feishu.send_alert.called, "崩溃未触发 [CRITICAL_ALERT] 通知"
        alert_msg = orch.feishu.send_alert.call_args.args[0]
        assert "[CRITICAL_ALERT]" in alert_msg, "告警消息缺少 [CRITICAL_ALERT] 标识"
        # loop still slept and honored the interval before the next cycle
        assert sleep_calls == [0], "容错恢复后未执行 time.sleep 进入下一周期"
        assert sched._running is False, "stop() 未生效"


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
