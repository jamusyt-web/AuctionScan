"""Listing source interface.

Every marketplace plugs in here. The contract is deliberately small: yield
Listing objects. How you got them -- an API, a run-list export, a CSV the
auction emailed you -- is the adapter's problem.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import Listing


class ListingSource(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self) -> Iterator[Listing]:
        """Yield listings currently available to bid on."""
        raise NotImplementedError

    def __iter__(self) -> Iterator[Listing]:
        return self.fetch()
