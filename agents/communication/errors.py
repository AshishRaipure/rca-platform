"""Communication Agent — errors."""
from __future__ import annotations


class CommunicationError(Exception):
    pass


class CommunicationInputError(CommunicationError):
    pass
