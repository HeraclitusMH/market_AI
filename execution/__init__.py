"""Execution routing layer — equity and options order placement.

Imports are kept lazy here to avoid loading ib_insync / trader.execution at
import time (they pull in heavy optional deps that aren't always available).
"""


def place_equity_order(intent, client=None, approve=True):
    from execution.equity_execution import place_equity_order as _fn
    return _fn(intent, client, approve)


def execute_options_intent(intent, client=None):
    from execution.options_execution import execute_options_intent as _fn
    return _fn(intent, client)
