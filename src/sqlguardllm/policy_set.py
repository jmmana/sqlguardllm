"""Policy sets: one reviewed file with a base policy and the roles that change it."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlguardllm.audit import AuditEvent, AuditLog
from sqlguardllm.guard import Guard
from sqlguardllm.policy import Policy, read_mapping
from sqlguardllm.result import PolicyError

EXTENDS = "extends"
ADD = "add"
_POLICY_FIELDS = frozenset(f.name for f in dataclasses.fields(Policy))


class PolicySet:
    """A ``base`` policy and named ``roles`` derived from it, loaded and validated once.

    A role is a mapping of policy keys plus two keys of its own::

        base:
          dialect: postgres
          tables: [invoices, branches]
          row_filters: {invoices: "branch_id IN ({user.branches})"}
        roles:
          manager:
            add:
              tables: [payroll]
          analyst:
            extends: manager
            max_rows: 100
            add:
              hidden_columns: [payroll.salary]

    Merge rules, applied from ``base`` down the ``extends`` chain:

    - A policy key set in a role **replaces** the inherited value, whether it is a scalar
      (``max_rows``), a list (``tables``) or a mapping (``row_filters``): what the role says
      is what it gets. ``row_filters: {}`` removes every inherited filter.
    - Keys under ``add`` **extend** what is inherited: lists are joined (union) and mappings
      are merged key by key, the role's value winning for a key in both. Adding to a scalar,
      to a key the role also sets, or to an inherited ``null`` (``tables: null``) is an error.
    - ``extends`` names the role to start from; without it a role starts from ``base``.

    Unknown roles, ``extends`` cycles and invalid policies raise :class:`PolicyError` when the
    set is built, not when a user with that role first arrives: every role is resolved and
    compiled up front.

    Args:
        base: The policy every role starts from.
        roles: Role name → changes to its parent policy, as described above.
    """

    def __init__(
        self, base: Policy, roles: Mapping[str, Mapping[str, Any] | None] | None = None
    ) -> None:
        if not isinstance(base, Policy):
            raise PolicyError("base must be a Policy")
        if roles is not None and not isinstance(roles, Mapping):
            raise PolicyError("roles must map role names to their changes")
        specs = dict(roles) if roles is not None else {}
        for name, spec in specs.items():
            if not isinstance(name, str) or not name.strip():
                raise PolicyError("role names must be non-empty strings")
            if spec is not None and not isinstance(spec, Mapping):
                raise PolicyError(f"role {name!r} must be a mapping of policy keys")
        self.base = base
        self._specs: dict[str, Mapping[str, Any]] = {
            name: spec if spec is not None else {} for name, spec in specs.items()
        }
        self._policies: dict[str, Policy] = {}
        for name in self._specs:
            self._resolve(name, ())
        self._guards: dict[tuple[str, str, bool], Guard] = {}
        for name in self._specs:
            self.guard(name)  # compile now: row filters, dialects and tables fail at load

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolicySet:
        """Build a policy set from ``{"base": {...}, "roles": {...}}``."""
        if not isinstance(data, Mapping):
            raise PolicyError("a policy set must be a mapping with base and roles")
        unknown = sorted(set(data) - {"base", "roles"})
        if unknown:
            raise PolicyError(f"unknown policy set keys: {', '.join(unknown)}")
        base = data.get("base")
        if not isinstance(base, Mapping):
            raise PolicyError("the policy set needs a base policy")
        return cls(Policy.from_dict(base), data.get("roles"))

    @classmethod
    def from_file(cls, path: str | Path) -> PolicySet:
        """Load a policy set from a ``.json``, ``.yaml`` or ``.yml`` file.

        YAML needs the optional extra: ``pip install "sqlguardllm[yaml]"``.
        """
        return cls.from_dict(read_mapping(path))

    @property
    def roles(self) -> tuple[str, ...]:
        """The role names, sorted."""
        return tuple(sorted(self._policies))

    def for_role(self, role: str) -> Policy:
        """The resolved policy of ``role``. An unknown role is a :class:`PolicyError`."""
        policy = self._policies.get(role) if isinstance(role, str) else None
        if policy is None:
            raise PolicyError(f"unknown role {role!r}")
        return policy

    def guard(
        self,
        role: str,
        *,
        language: str = "en",
        pretty: bool = False,
        audit: AuditLog | Callable[[AuditEvent], None] | None = None,
    ) -> Guard:
        """A :class:`Guard` for ``role``, compiled once and cached per language and ``pretty``.

        With ``audit`` the Guard is new each time (it is not cached) and its events carry the
        role name.
        """
        key = (role, language, pretty)
        guard = self._guards.get(key) if audit is None else None
        if guard is None:
            policy = self.for_role(role)
            try:
                guard = Guard(policy, language=language, pretty=pretty, audit=audit, role=role)
            except PolicyError as exc:
                raise PolicyError(f"role {role!r}: {exc}") from exc
            if audit is None:
                self._guards[key] = guard
        return guard

    def _resolve(self, name: str, chain: tuple[str, ...]) -> Policy:
        resolved = self._policies.get(name)
        if resolved is not None:
            return resolved
        if name in chain:
            cycle = " -> ".join((*chain[chain.index(name) :], name))
            raise PolicyError(f"roles extend each other in a cycle: {cycle}")
        spec = self._specs[name]
        parent_name = spec.get(EXTENDS)
        if parent_name is None:
            parent = self.base
        elif isinstance(parent_name, str) and parent_name in self._specs:
            parent = self._resolve(parent_name, (*chain, name))
        else:
            raise PolicyError(f"role {name!r} extends unknown role {parent_name!r}")
        policy = _derive(parent, spec, name)
        self._policies[name] = policy
        return policy


def _derive(parent: Policy, spec: Mapping[str, Any], role: str) -> Policy:
    """Apply one role's changes to its parent policy."""
    unknown = sorted(set(spec) - _POLICY_FIELDS - {EXTENDS, ADD})
    if unknown:
        raise PolicyError(f"unknown keys in role {role!r}: {', '.join(unknown)}")
    changes = {key: value for key, value in spec.items() if key in _POLICY_FIELDS}
    additions = spec.get(ADD) or {}
    if not isinstance(additions, Mapping):
        raise PolicyError(f"add in role {role!r} must map policy keys to what is added")

    for key, extra in additions.items():
        if key not in _POLICY_FIELDS:
            raise PolicyError(f"unknown key in add of role {role!r}: {key}")
        if key in changes:
            raise PolicyError(f"role {role!r} both sets and adds {key}: choose one")
        current = getattr(parent, key)
        if isinstance(current, Mapping):
            if not isinstance(extra, Mapping):
                raise PolicyError(f"add.{key} in role {role!r} must be a mapping")
            changes[key] = MappingProxyType({**current, **extra})
        elif isinstance(current, frozenset):
            if isinstance(extra, str) or not isinstance(extra, Collection):
                raise PolicyError(f"add.{key} in role {role!r} must be a list")
            try:
                changes[key] = current | frozenset(extra)
            except TypeError as exc:
                raise PolicyError(f"add.{key} in role {role!r} must contain strings") from exc
        elif current is None:
            raise PolicyError(
                f"role {role!r} can't add to {key}: the inherited value is null, set it instead"
            )
        else:
            raise PolicyError(f"role {role!r} can only add to lists and mappings, not {key}")

    try:
        return dataclasses.replace(parent, **changes)
    except PolicyError as exc:
        raise PolicyError(f"role {role!r}: {exc}") from exc
