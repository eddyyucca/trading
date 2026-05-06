from app.db.init_db import init_db, seed_defaults
from app.db.session import SessionLocal
from app.services.jobs import backfill

if __name__ == '__main__':
    init_db()
    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
    print(backfill(limit=300))
