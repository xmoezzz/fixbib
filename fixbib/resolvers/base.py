from __future__ import annotations

from abc import ABC, abstractmethod

from fixbib.model import BibCandidate, BibInputEntry


class Resolver(ABC):
    name: str

    @abstractmethod
    def can_resolve(self, entry: BibInputEntry) -> bool:
        raise NotImplementedError

    @abstractmethod
    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        raise NotImplementedError
