"""Concurrency tests need real row locks — run against Postgres:
    DATABASE_URL=postgres://... python manage.py test sales.tests.test_concurrency
They are skipped on SQLite."""
import threading
import unittest

from django.db import connection, connections
from django.test import TransactionTestCase

from ims.testutils import World
from sales import services


@unittest.skipUnless(connection.vendor == "postgresql", "needs PostgreSQL row locks")
class ConcurrentCheckoutTests(TransactionTestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.reg2 = self.w.a.registers.create(code="R2", name="Till 2")
        self.w.open_shift(self.reg2, self.w.manager_a)

    def _run(self, carts):
        errors, results = [], []
        barrier = threading.Barrier(len(carts))

        def worker(register, lines):
            try:
                barrier.wait()
                results.append(self.w.sell(*lines, register=register))
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=c) for c in carts]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results, errors

    def test_opposite_order_carts_do_not_deadlock(self):
        carts = [(self.w.reg_a, [(self.w.p1, 1), (self.w.p2, 1)]), (self.reg2, [(self.w.p2, 1), (self.w.p1, 1)])] * 4
        results, errors = self._run(carts)
        self.assertEqual(errors, [])
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 2)

    def test_no_oversell(self):
        carts = [(self.w.reg_a if i % 2 else self.reg2, [(self.w.p1, 3)]) for i in range(6)]
        results, errors = self._run(carts)
        self.assertEqual(len(results), 3)  # 10 on hand / 3 each
        self.assertTrue(all(isinstance(e, services.StockUnavailable) for e in errors))
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 1)
