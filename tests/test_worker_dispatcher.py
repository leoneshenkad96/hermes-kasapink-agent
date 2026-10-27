import unittest

from worker_dispatcher import UnknownWorker, WorkerDispatcher


class WorkerDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_registered_async_worker(self):
        dispatcher = WorkerDispatcher()

        async def worker(value):
            return value.upper()

        dispatcher.register("conversation", worker)
        self.assertEqual(await dispatcher.dispatch("conversation", "ok"), "OK")

    async def test_rejects_unknown_worker(self):
        with self.assertRaises(UnknownWorker):
            await WorkerDispatcher().dispatch("missing")

    async def test_duplicate_registration_is_rejected(self):
        dispatcher = WorkerDispatcher()
        dispatcher.register("one", lambda: "ok")
        with self.assertRaises(ValueError):
            dispatcher.register("ONE", lambda: "again")


if __name__ == "__main__":
    unittest.main()
