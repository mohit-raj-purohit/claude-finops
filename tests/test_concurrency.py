"""The HTTP server is threaded; Analytics must survive concurrent queries.

Before this test existed, one sqlite connection was shared across every request
thread and a page that fired several API calls at once failed roughly half of them
with "bad parameter or other API misuse".

Run: python3 -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(n_rows=2000):
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta (key, value) VALUES ('built_at', 'test')")
    db.executemany(
        """INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model,
                                 priced_as, context_tokens, est_cost_usd, billable_tokens)
           VALUES (?, 's', 1, 1, ?, '2026-01-01', 'm', 'm', ?, ?, ?)""",
        [(f"u{i}", f"2026-01-01T00:00:{i % 60:02d}Z", 1000 + i, 0.01, 10) for i in range(n_rows)])
    db.commit()
    db.close()
    return path


class TestConcurrentQueries(unittest.TestCase):
    def setUp(self):
        self.path = make_db()
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.a = Analytics(self.path)

    def test_many_threads_query_at_once_without_error(self):
        errors, results = [], []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(25):
                    n = self.a.one("SELECT COUNT(*) n, SUM(est_cost_usd) c FROM requests")
                    rows = self.a.q("SELECT day, SUM(est_cost_usd) c FROM requests GROUP BY 1")
                    with lock:
                        results.append((n["n"], len(rows)))
            except Exception as e:      # noqa: BLE001 - we want the real failure type
                with lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], f"concurrent queries failed: {errors[:3]}")
        self.assertEqual(len(results), 12 * 25)
        self.assertTrue(all(r == (2000, 1) for r in results))

    def test_each_thread_gets_its_own_connection(self):
        seen = {}

        def worker(i):
            seen[i] = id(self.a.db)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(seen.values())), 4)

    def test_close_releases_every_connection_and_reopens_lazily(self):
        def touch():
            self.a.one("SELECT 1 x")

        threads = [threading.Thread(target=touch) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        touch()
        self.a.close()
        self.assertEqual(self.a._conns, [])
        # still usable afterwards: a fresh connection is opened on demand
        self.assertEqual(self.a.one("SELECT COUNT(*) n FROM requests")["n"], 2000)


if __name__ == "__main__":
    unittest.main()
