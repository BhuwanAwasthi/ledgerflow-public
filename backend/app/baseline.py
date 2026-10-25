"""The shared baseline every user starts from, and each user's independent copy of it.

- Baseline files (scenario PDFs, supporting documents, their page renders) are stored once under
  `baseline/` in storage and are read-only for users: a scenario case's document points at the shared file,
  nothing is copied per user. They are published lazily, the first time any user needs one, through the
  base storage's `put_baseline` (OwnerStorage cannot write there).
- Baseline master data (vendors, POs, GRNs, GL accounts, seeded history) is copied into the user's
  workspace on first sign-in (`ensure_workspace`), because users change it (PO balances, vendor approvals).
  "Reset demo data" wipes the workspace and seeds it again (`reset_workspace`).
- `BASELINE_VERSION` is stored on the user when their workspace is seeded; a higher constant here means new
  demo data is available (GET /api/me reports it, and a reset picks it up).
- Scenario prerequisites: S02b builds on S02a, S04 on S01 (`requires` in data/scenarios.yaml). Running one
  without its prerequisite in the workspace runs the prerequisite first (`scenario_cases`).
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import pdf
from app.agents.intake import render_params
from app.cases import StoredFile, create_case
from app.config_loader import ResolvedConfig
from app.db import Base
from app.models import Case, CaseStatus, User
from app.scenarios import Scenarios, ScenarioSpec, load_scenarios
from app.seed import SeedReport, load_master, seed_history
from app.storage import BASELINE_PREFIX, BaseStorage, Storage
from app.tenancy import bind_owner, require_owner, scoped

BASELINE_VERSION = 1
FIXTURE_PREFIX = f"{BASELINE_PREFIX}fixtures/"
KEEP_ON_RESET = frozenset({"config_versions"})  # Agent Studio edits are config, not demo data (P3 resets)
BUSY = (CaseStatus.received, CaseStatus.processing)


class BaselineError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BaselineFile:
    filename: str
    key: str  # baseline/fixtures/<sha12>_<filename>: a changed fixture gets a new key, old cases keep theirs
    sha256: str

    def stored(self) -> StoredFile:
        return StoredFile(self.filename, "application/pdf", self.key, self.sha256)


class Baseline:
    def __init__(self, storage: BaseStorage, data_dir: Path, config: ResolvedConfig):
        self.storage = storage
        self.fixture_dir = data_dir / "fixtures"
        self.master_dir = data_dir / "master"
        self.scenarios: Scenarios = load_scenarios(data_dir / "scenarios.yaml")
        self.config = config  # baseline config (files only): render settings, seeded history
        self._published: dict[str, BaselineFile] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- shared files
    def known_files(self) -> set[str]:
        return {f for sc in self.scenarios.scenarios for f in (sc.file, *(x.file for x in sc.supporting_files))}

    def available(self, filename: str) -> bool:
        return (self.fixture_dir / filename).is_file()

    def fixture(self, filename: str) -> BaselineFile:
        """The shared stored copy of a scenario file, published (with its page renders) on first use."""
        if filename not in self.known_files():
            raise BaselineError(404, f"{filename} is not a scenario file")
        with self._lock:
            if filename in self._published:
                return self._published[filename]
            path = self.fixture_dir / filename
            if not path.is_file():
                raise BaselineError(409, f"{filename} has not been generated: run `make fixtures`")
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            bf = BaselineFile(filename, f"{FIXTURE_PREFIX}{sha[:12]}_{filename}", sha)
            try:  # the text layer is written last, so it marks a complete publish
                self.storage.get(pdf.text_key(sha, BASELINE_PREFIX))
            except FileNotFoundError:
                self._publish(bf, data)
            self._published[filename] = bf
            return bf

    def _publish(self, bf: BaselineFile, data: bytes) -> None:
        dpi, max_pages = render_params(self.config.app.limits, self.config.agents["intake"].params)
        self.storage.put_baseline(bf.key, data, "application/pdf")
        rendered = pdf.render(data, dpi, max_pages)
        for i, png in enumerate(rendered.pngs, start=1):
            self.storage.put_baseline(pdf.page_png_key(bf.sha256, i, BASELINE_PREFIX), png, "image/png")
        self.storage.put_baseline(pdf.text_key(bf.sha256, BASELINE_PREFIX), pdf.dump_texts(rendered.texts),
                                  "application/json")

    def read(self, filename: str) -> bytes:
        return self.storage.get(self.fixture(filename).key)

    # ---------------------------------------------------------------- workspaces
    def _seed(self, s: Session) -> SeedReport:
        rep = load_master(s, self.master_dir)
        rep.history_cases = seed_history(s, self.config, self.scenarios)
        return rep

    def ensure_workspace(self, sf: sessionmaker[Session], user_id: str) -> User:
        """Seed the user's workspace once (idempotent; a concurrent second seed loses on the composite
        uniques and rolls back)."""
        with bind_owner(sf, user_id)() as s:
            user = s.get(User, user_id)
            if user is None:
                raise BaselineError(404, "unknown user")
            if user.baseline_version is None:
                try:
                    self._seed(s)
                    user.baseline_version = BASELINE_VERSION
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    s.refresh(user)
            return user

    def reset_workspace(self, sf: sessionmaker[Session], user_id: str, storage: Storage) -> SeedReport:
        """Wipe the user's demo data and files and seed the baseline again. Refused while a run is in flight
        (its stage writes would land on deleted rows)."""
        with bind_owner(sf, user_id)() as s:
            busy = s.scalars(scoped(select(Case.ref).where(Case.status.in_(BUSY)), user_id)).all()
            if busy:
                raise BaselineError(409, f"wait for the running case(s) to finish first: {', '.join(r or '' for r in busy)}")
            wipe_workspace(s)
            user = s.get(User, user_id)
            assert user is not None
            user.case_seq = user.export_seq = 0
            rep = self._seed(s)
            user.baseline_version = BASELINE_VERSION
            s.commit()
        storage.delete_prefix("")
        return rep

    # ---------------------------------------------------------------- scenarios
    def missing_prerequisites(self, s: Session, scenario: ScenarioSpec) -> list[ScenarioSpec]:
        """Prerequisites with no (non-failed) run in the session owner's workspace, in run order."""
        deps = self.scenarios.prerequisites(scenario)
        if not deps:
            return []
        ran = set(s.scalars(scoped(
            select(Case.scenario_id).where(
                Case.scenario_id.in_([d.id for d in deps]), Case.status != CaseStatus.failed
            ),
            require_owner(s),
        )).all())
        return [d for d in deps if d.id not in ran]

    def submitter(self, scenario: ScenarioSpec) -> tuple[str, str]:
        """The vendor's billing contact when the file is from one vendor, else the buyer's AP mailbox."""
        sc = self.scenarios
        invoices = sc.invoices(scenario)
        if len(invoices) == 1:
            inv = invoices[0]
            if inv.vendor:
                v = sc.vendor(inv.vendor)
                return v.legal_name, f"billing@{v.email_domain}"
            if scenario.new_vendor:
                return scenario.new_vendor.legal_name, f"billing@{scenario.new_vendor.email_domain}"
        return f"{sc.buyer.name} mailroom", sc.buyer.ap_email

    def scenario_cases(self, s: Session, cfg: ResolvedConfig, storage: Storage, scenario: ScenarioSpec) -> list[Case]:
        """New cases for `scenario`, preceded by its missing prerequisites (run order). Committed together.
        Each case's document points at the shared baseline file."""
        prereqs = self.missing_prerequisites(s, scenario)
        s.rollback()  # end the read transaction: publishing (render + upload) must never hold the database
        files = {spec.id: self.fixture(spec.file) for spec in [*prereqs, scenario]}
        cases: list[Case] = []
        for spec in [*prereqs, scenario]:
            name, email = self.submitter(spec)
            note = f"Scenario {spec.id}: {spec.title}"
            if spec is scenario and prereqs:
                note += (f". Ran {', '.join(p.id for p in prereqs)} first: this scenario builds on it "
                         "and your workspace had no run of it yet.")
            elif spec is not scenario:
                note += f". Run automatically before {scenario.id}, which builds on it."
            cases.append(create_case(
                s, cfg, storage, [files[spec.id].stored()], submitter_name=name, submitter_email=email,
                note=note, source="fixture", scenario_id=spec.id, commit=False,
            ))
        s.commit()
        return cases


def wipe_workspace(s: Session) -> None:
    """Delete every row of the session owner's workspace (config overrides excepted), children first."""
    owner = require_owner(s)
    for table in reversed(Base.metadata.sorted_tables):
        if "owner_id" in table.c and table.name not in KEEP_ON_RESET:
            s.execute(table.delete().where(table.c.owner_id == owner))
