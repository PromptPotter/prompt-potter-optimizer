from dataclasses import dataclass, field


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
