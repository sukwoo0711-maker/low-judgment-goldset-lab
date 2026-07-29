from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from goldset_lab.run_lock import acquire_run_lock


class RunLockTests(unittest.TestCase):
    def test_live_owner_blocks_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "run.lock"
            acquire_run_lock(lock, "a")
            with self.assertRaises(RuntimeError):
                acquire_run_lock(lock, "a")

    def test_stale_lock_needs_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "run.lock"
            lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                acquire_run_lock(lock, "a")
            acquire_run_lock(lock, "a", recover_stale=True)


if __name__ == "__main__":
    unittest.main()
