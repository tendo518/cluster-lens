"""Public application node metadata. Deployment variables never enter this model."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Host:
    name: str
    address: str
    groups: tuple[str, ...] = ()

    def public(self):
        return {"name": self.name, "address": self.address, "groups": list(self.groups)}
