# SPDX-License-Identifier: AGPL-3.0-or-later
"""The signature gate's REPORTING — what a refused task leaves in the log.

Refusing is deny-by-default and correct; these tests cover the part that was
missing, namely that a refusal which also kills a recurring schedule says so.
The gate's decision itself is exercised end-to-end in ``tests/test_step.py``.
"""

def test_rejection_names_the_broken_schedule_for_a_recurring_task():
    """A refused RECURRING task must say its schedule died — silence is the bug.

    The regression: four daily hygiene routines were refused at the gate, went
    to `rechazada`, and therefore never minted their next occurrence. The log
    said only "signature verification failed" — nothing that told the owner the
    whole schedule had stopped, so it stopped unnoticed.
    """
    from lhassa.core.step import _rejection_resultado
    from lhassa.core.watchlist import Trigger

    trigger = Trigger(kind="task", action="temple_run", recurrence="86400")
    text = _rejection_resultado(trigger, "signature verification failed")

    assert "signature verification failed" in text
    assert "stops here" in text
    assert "temple_run" in text


def test_rejection_of_a_one_shot_task_stays_terse():
    """No recurrence, no schedule to lose — the bare reason is the whole story."""
    from lhassa.core.step import _rejection_resultado
    from lhassa.core.watchlist import Trigger

    trigger = Trigger(kind="task", action="send_reminder")
    assert _rejection_resultado(trigger, "signature verification failed") == (
        "signature verification failed"
    )


def test_rejection_text_never_raises_on_a_broken_trigger():
    from lhassa.core.step import _rejection_resultado

    class Broken:
        @property
        def recurrence(self):
            raise RuntimeError("boom")

    assert _rejection_resultado(Broken(), "reason") == "reason"
