"""Regression: the record-access audit write must not wait for a connection held by the request it audits.

Found while taking screenshots: a browser opening an athlete record fires several record requests at once; each held
a pooled connection while its audit flush asked the same pool for another, so the pool (5 locally, 2 hosted) ran dry
and every request stalled until the pool timeout. Audit writes now use their own one-connection pool.
"""
import threading
import time

from sqlalchemy import func, select

from .conftest import OWNER_LOGIN, fresh_client, login, make_athlete


def test_parallel_record_requests_do_not_exhaust_the_pool_and_are_all_audited(app):
    from server.v2.models import RecordAccess
    c = fresh_client(app)
    h = login(c, OWNER_LOGIN)
    aid = make_athlete(c, h, code="POOL-1")
    with app.state.DB() as db:
        before = db.scalar(select(func.count()).select_from(RecordAccess).where(RecordAccess.athlete_id == aid))
    cookies = dict(c.cookies)
    results, n = [], 12
    barrier = threading.Barrier(n)

    def hit():
        x = fresh_client(app)
        x.cookies.update(cookies)
        barrier.wait()
        t0 = time.monotonic()
        r = x.get(f"/api/athletes/{aid}")
        results.append((r.status_code, time.monotonic() - t0))

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(results) == n and all(code == 200 for code, _ in results), results
    assert max(s for _, s in results) < 8, results  # the pool timeout is 15 s; a deadlock shows up as ~15 s
    with app.state.DB() as db:
        after = db.scalar(select(func.count()).select_from(RecordAccess).where(RecordAccess.athlete_id == aid))
    assert after - before == n
    assert app.state.AuditDB is not app.state.DB
