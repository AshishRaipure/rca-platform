"""RCA Agent — errors."""
from __future__ import annotations


class RcaError(Exception):
    pass


class RcaInputError(RcaError):
    pass


class RcaUnavailableError(RcaError):
    """The model could not be reached or parsed; the node degrades and the confidence gate escalates."""
