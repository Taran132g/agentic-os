"""
PAIS local-business workflows.

A small agent fleet sold to local SMBs (hospitality / fitness / personal-care),
each agent plugging one revenue leak. Runs on the customer's machine via their
own Claude subscription; the PAIS website is the owner's approve-and-watch
surface. See ARCHITECTURE.md in trappe-demo/ for the full picture.
"""

__all__ = [
    "business", "state", "base", "runtime",
    "reputation_workflow", "reactivation_workflow", "missedcall_workflow",
    "reminders_workflow", "digest_workflow",
]
