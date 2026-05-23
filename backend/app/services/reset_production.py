from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Cycle, Event, Machine, Mold, MoldMachine


def wipe_production_history(db: Session) -> dict[str, int]:
    """Clear cycles, events, molds; unlink machines. Keeps camera/machine config."""
    db.query(Machine).update({"current_mold_id": None}, synchronize_session=False)
    deleted_cycles = db.query(Cycle).delete(synchronize_session=False)
    deleted_events = db.query(Event).delete(synchronize_session=False)
    deleted_links = db.query(MoldMachine).delete(synchronize_session=False)
    deleted_molds = db.query(Mold).delete(synchronize_session=False)
    db.commit()
    return {
        "deleted_cycles": int(deleted_cycles),
        "deleted_events": int(deleted_events),
        "deleted_mold_links": int(deleted_links),
        "deleted_molds": int(deleted_molds),
    }
