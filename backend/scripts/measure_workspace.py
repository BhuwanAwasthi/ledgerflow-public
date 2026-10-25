"""Measure how much one new workspace (master data + sample history) adds to the database.

    uv --directory backend run python -m scripts.measure_workspace [--users 3]

Seeds N workspaces into a fresh, migrated SQLite file and VACUUMs after each one, so the growth per
workspace is real on-disk bytes. Postgres stores the same rows with more per-row overhead (tuple header,
item pointer, one entry per index), so the report adds a deliberately generous 200 bytes per row as a
Postgres estimate. Supabase free gives 500 MB; the budget per workspace is 200 KB (tests/test_seed.py).
"""

from __future__ import annotations

import argparse
import itertools
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select

from app.baseline import Baseline
from app.config_loader import resolved_config
from app.db import Base, init_db, make_engine, make_session_factory
from app.models import User
from app.settings import REPO_ROOT
from app.storage import LocalStorage

PG_ROW_OVERHEAD_B = 200
BUDGET_B = 200 * 1024


@dataclass
class Footprint:
    sqlite_bytes: int  # median on-disk growth per workspace
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return sum(self.rows.values())

    @property
    def postgres_estimate_bytes(self) -> int:
        return self.sqlite_bytes + self.row_count * PG_ROW_OVERHEAD_B


def _vacuumed_size(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return db.stat().st_size


def measure(users: int = 3, workdir: Path | None = None) -> Footprint:
    root = Path(workdir or tempfile.mkdtemp(prefix="lf-measure-"))
    db = root / "measure.db"
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    try:
        init_db(engine)
        sf = make_session_factory(engine)
        baseline = Baseline(LocalStorage(root / "storage"), REPO_ROOT / "data", resolved_config(REPO_ROOT / "config"))
        sizes = [_vacuumed_size(db)]
        for i in range(users):
            uid = f"00000000-0000-4000-8000-{i:012d}"
            with sf() as s:
                s.add(User(id=uid, email=f"measure{i}@example.com"))
                s.commit()
            baseline.ensure_workspace(sf, uid)
            sizes.append(_vacuumed_size(db))
        growth = sorted(b - a for a, b in itertools.pairwise(sizes))
        rows: dict[str, int] = {}
        with sf() as s:
            for table in Base.metadata.sorted_tables:
                if "owner_id" in table.c and table.name != "users":
                    n = s.scalar(select(func.count()).select_from(table).where(table.c.owner_id == uid)) or 0
                    if n:
                        rows[table.name] = n
        return Footprint(sqlite_bytes=growth[len(growth) // 2], rows=rows)
    finally:
        engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--users", type=int, default=3, help="workspaces to seed (the median growth is reported)")
    fp = measure(ap.parse_args().users)
    print("rows per workspace: " + ", ".join(f"{k}={v}" for k, v in fp.rows.items()) + f" (total {fp.row_count})")
    print(f"SQLite, measured:   {fp.sqlite_bytes / 1024:.1f} KB per new workspace")
    print(f"Postgres, estimate: {fp.postgres_estimate_bytes / 1024:.1f} KB per new workspace "
          f"(+{PG_ROW_OVERHEAD_B} B/row overhead)")
    print(f"budget:             {BUDGET_B / 1024:.0f} KB -> {'OK' if fp.postgres_estimate_bytes < BUDGET_B else 'OVER'}")


if __name__ == "__main__":
    main()
