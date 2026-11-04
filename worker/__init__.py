"""gRNAde worker.

Deployed separately from the control plane as an immutable image. It must not
import the ``app`` package — the worker and control plane share only read-only
code and assets, never process state.
"""
