"""五轴夹具：每轴各写各的。

G23 的 axis_reuse 门禁会拒掉五轴一字不差的报告，所以夹具必须真的答了五个维度。
"""

from __future__ import annotations

PRINCIPLE_KEYS = (
    "runtime_control_flow",
    "data_flow",
    "state_lifecycle",
    "failure_recovery",
    "concurrency_timing",
)

PRIN = {
    "runtime_control_flow":
        "The call site owns the loop and decides when to stop: each attempt is made from a single entry point, the loop returns early once the budget is exhausted, and no recursion is used so the depth of the call stack never grows with the number of attempts",
    "data_flow":
        "The delay value starts as a configuration constant, is multiplied by two after every failed attempt, has a random fraction added on top, and is then converted from milliseconds into the native duration type that the timer implementation expects before it reaches the scheduler",
    "state_lifecycle":
        "The attempt counter is created when the wrapper is entered, is incremented once per completed attempt, and is discarded when the wrapper returns, so two concurrent callers never observe each other's progress and no counter survives the lifetime of a single call",
    "failure_recovery":
        "A failure is detected when the transport raises a retryable error, is classified by consulting an allow list of exception types, and is either swallowed so the loop can try again or is re-raised unchanged once the budget no longer permits another attempt",
    "concurrency_timing":
        "Only one attempt is in flight at a time per caller, the wait between attempts is scheduled on a timer rather than a sleeping thread, and the jitter term prevents a fleet of clients that failed together from retrying at the same instant and hammering the same server",
}
for _k in PRINCIPLE_KEYS:
    assert len(PRIN[_k].split()) >= 40, f"轴 {_k} 必须 >= 40 单位"
