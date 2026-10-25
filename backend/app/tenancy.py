"""Workspace isolation: every read and write of a tenant table is scoped to one owner.

Two layers, so a forgotten filter cannot leak another user's rows:
1. `scoped(stmt, owner)` adds `owner_id = :owner` for every tenant entity a query selects. Every repository
   query site calls it (API handlers, agents, ledger, exports, portal).
2. Owner-bound sessions (`bind_owner(sf, owner_id)`, used for every request and every pipeline job) enforce
   the same thing underneath: a `do_orm_execute` hook applies the owner criteria to every ORM SELECT, UPDATE
   and DELETE on a tenant table (including `Session.get`), and a `before_flush` hook stamps `owner_id` on new
   rows and refuses rows that belong to someone else.

Unbound sessions (no owner) are for system code only: migrations, the startup resume of queued jobs, the
auth gate's user upsert and stream-token check. They may not insert a tenant row without an explicit owner.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import FromClause, Join, Select, Table, event, or_, update
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker, with_loader_criteria
from sqlalchemy.orm.attributes import get_history

from app.models import TenantMixin, User

OWNER_KEY = "owner_id"


class TenancyError(RuntimeError):
    """A write that would cross workspaces, or a tenant row without an owner (a bug, never user input)."""


def owner_id_of(owner: User | str) -> str:
    return owner if isinstance(owner, str) else owner.id


def session_owner(s: Session) -> str | None:
    return s.info.get(OWNER_KEY)


def require_owner(s: Session) -> str:
    oid = session_owner(s)
    if oid is None:
        raise TenancyError("this code path needs an owner-bound session (app.tenancy.bind_owner)")
    return oid


def bind_owner(sf: sessionmaker[Session], owner: User | str) -> sessionmaker[Session]:
    """A session factory whose sessions only see and write `owner`'s rows."""
    return sessionmaker(**{**sf.kw, "info": {**(sf.kw.get("info") or {}), OWNER_KEY: owner_id_of(owner)}})


def _tables(frm: FromClause, nullable: bool = False) -> list[tuple[Table, bool]]:
    """(table, on the nullable side of an outer join) for the tables of a FROM item."""
    if isinstance(frm, Table):
        return [(frm, nullable)]
    if isinstance(frm, Join):
        return _tables(frm.left, nullable or frm.full) + _tables(frm.right, nullable or frm.isouter)
    return []  # a subquery is scoped where it is built


def scoped[*Ts](stmt: Select[*Ts], owner: User | str) -> Select[*Ts]:
    """`stmt` filtered to `owner` for every tenant table it selects from (joined tables included; the
    nullable side of an outer join may also be missing, so the join keeps its meaning)."""
    oid = owner_id_of(owner)
    seen: set[Table] = set()
    for frm in stmt.get_final_froms():
        for table, nullable in _tables(frm):
            if table not in seen and "owner_id" in table.c:
                seen.add(table)
                col = table.c.owner_id
                stmt = stmt.where(or_(col == oid, col.is_(None)) if nullable else col == oid)
    return stmt


def next_seq(s: Session, owner: User | str, kind: str) -> int:
    """Per-owner counter (`case` | `export`): INV-2026-0001 restarts for every workspace. Atomic (a single
    UPDATE ... RETURNING), so two concurrent runs of one user never get the same number."""
    column = {"case": User.case_seq, "export": User.export_seq}[kind]
    n = s.execute(
        update(User).where(User.id == owner_id_of(owner)).values({column: column + 1}).returning(column)
    ).scalar_one()
    return int(n)


# ---------------------------------------------------------------- session guards
@event.listens_for(Session, "do_orm_execute")
def _owner_criteria(state: ORMExecuteState) -> None:
    oid = session_owner(state.session)
    if oid is None or state.is_column_load or state.is_relationship_load:
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(TenantMixin, lambda cls: cls.owner_id == oid, include_aliases=True)
        )


@event.listens_for(Session, "before_flush")
def _stamp_owner(session: Session, flush_context: Any, instances: Any) -> None:
    oid = session_owner(session)
    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        if obj.owner_id is None:
            if oid is None:
                raise TenancyError(f"{type(obj).__name__} inserted without an owner")
            obj.owner_id = oid
        elif oid is not None and obj.owner_id != oid:
            raise TenancyError(f"{type(obj).__name__} inserted for another owner")
    for obj in session.dirty:
        if isinstance(obj, TenantMixin) and get_history(obj, "owner_id").has_changes():
            raise TenancyError(f"{type(obj).__name__}.owner_id cannot change")
