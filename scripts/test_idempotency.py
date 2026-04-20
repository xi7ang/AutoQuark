#!/usr/bin/env python3
"""Test idempotency logic for quark-mswnlz-publisher.

Run from the skill scripts directory:
  cd /root/.openclaw/workspace/skills/quark-mswnlz-publisher/scripts
  python3 test_idempotency.py

Tests:
  1. Cross-batch URL dedup (link_registry.json)
  2. Per-batch transferred/shared state + recovery
  3. Telegram notification dedup per group
  4. Repo update dedup per batch
"""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

# inject skill scripts dir into sys.path so we can import _state
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# point to the real workspace batch_run_states dir
BATCH_DIR = Path("/root/.openclaw/workspace/batch_run_states")

# ── setup/teardown ─────────────────────────────────────────────────────────

def wipe_states():
    if BATCH_DIR.exists():
        shutil.rmtree(BATCH_DIR)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)


class TestIdempotency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wipe_states()

    def setUp(self):
        wipe_states()

    # ── Test 1: link_registry (cross-batch dedup) ────────────────────────────

    def test_url_not_processed_initially(self):
        from _state import is_url_processed
        self.assertFalse(is_url_processed("https://pan.quark.cn/s/abc123"))

    def test_url_registered_after_processing(self):
        from _state import is_url_processed, register_url, get_share_from_registry
        url = "https://pan.quark.cn/s/test_url_001"
        self.assertFalse(is_url_processed(url))
        register_url(url, title="测试资源", share_url="https://pan.quark.cn/s/xyz789", batch_id="batch_001")
        self.assertTrue(is_url_processed(url))

    def test_url_registered_different_batch_same_url(self):
        from _state import is_url_processed, register_url, get_share_from_registry
        url = "https://pan.quark.cn/s/test_url_002"
        register_url(url, title="资源A", share_url="https://pan.quark.cn/s/shareA", batch_id="batch_A")
        self.assertTrue(is_url_processed(url))
        cached = get_share_from_registry(url)
        self.assertEqual(cached["batch_id"], "batch_A")
        self.assertEqual(cached["share_url"], "https://pan.quark.cn/s/shareA")

    def test_link_registry_persists_to_disk(self):
        from _state import is_url_processed, register_url
        url = "https://pan.quark.cn/s/test_url_003"
        register_url(url, title="持久化测试", share_url="https://pan.quark.cn/s/share003", batch_id="batch_X")
        # reload module to simulate new process
        import importlib
        import _state
        importlib.reload(_state)
        from _state import is_url_processed as is_proc
        self.assertTrue(is_proc(url))

    # ── Test 2: per-batch transferred + shared state ────────────────────────

    def test_new_batch_has_empty_state(self):
        from _state import load_batch_state
        state = load_batch_state("batch_new_001")
        self.assertEqual(state["transferred"], {})
        self.assertEqual(state["shared"], {})
        self.assertEqual(state["repos_updated"], [])
        self.assertEqual(state["tg_notified"], {})

    def test_mark_transferred_persists(self):
        from _state import mark_transferred, is_transferred, get_transferred_fid
        batch = "batch_002"
        mark_transferred(batch, "资源甲", fid="fid_甲", input_url="https://pan.quark.cn/s/甲")
        self.assertTrue(is_transferred(batch, "资源甲"))
        self.assertEqual(get_transferred_fid(batch, "资源甲"), "fid_甲")
        # 未转存的返回 None
        self.assertIsNone(get_transferred_fid(batch, "资源乙"))

    def test_mark_shared_after_transferred(self):
        from _state import mark_transferred, mark_shared, is_item_complete_for_batch
        batch = "batch_003"
        mark_transferred(batch, "资源B", fid="fid_B", input_url="https://pan.quark.cn/s/B")
        self.assertFalse(is_item_complete_for_batch(batch, "资源B"))
        mark_shared(batch, "资源B", share_url="https://pan.quark.cn/s/B_share", share_id="sid_B", fid="fid_B")
        self.assertTrue(is_item_complete_for_batch(batch, "资源B"))

    def test_recover_share_results(self):
        from _state import mark_transferred, mark_shared, recover_share_results
        batch = "batch_004"
        mark_transferred(batch, "资源C", fid="fid_C", input_url="https://pan.quark.cn/s/C")
        mark_shared(batch, "资源C", share_url="https://pan.quark.cn/s/C_share", share_id="sid_C", fid="fid_C")
        results = recover_share_results(batch)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "资源C")
        self.assertEqual(results[0]["share_url"], "https://pan.quark.cn/s/C_share")

    def test_state_persists_to_disk_after_reload(self):
        from _state import mark_transferred, mark_shared
        batch = "batch_005"
        mark_transferred(batch, "资源D", fid="fid_D", input_url="https://pan.quark.cn/s/D")
        mark_shared(batch, "资源D", share_url="https://pan.quark.cn/s/D_share", share_id="sid_D", fid="fid_D")
        import importlib
        import _state
        importlib.reload(_state)
        from _state import is_item_complete_for_batch, recover_share_results
        self.assertTrue(is_item_complete_for_batch(batch, "资源D"))
        results = recover_share_results(batch)
        self.assertEqual(results[0]["share_url"], "https://pan.quark.cn/s/D_share")

    # ── Test 3: Telegram notification dedup ──────────────────────────────────

    def test_no_group_notified_initially(self):
        from _state import get_tg_notified_groups, is_tg_notified
        self.assertEqual(get_tg_notified_groups("batch_tg_001"), [])
        self.assertFalse(is_tg_notified("batch_tg_001", "-100111222333"))

    def test_mark_tg_notified_records_group(self):
        from _state import mark_tg_notified, get_tg_notified_groups, is_tg_notified
        batch = "batch_tg_002"
        mark_tg_notified(batch, "-100111222333", chunks=3)
        groups = get_tg_notified_groups(batch)
        self.assertIn("-100111222333", groups)
        self.assertTrue(is_tg_notified(batch, "-100111222333"))
        self.assertFalse(is_tg_notified(batch, "-100999888777"))

    def test_tg_notified_persists_after_reload(self):
        from _state import mark_tg_notified, is_tg_notified
        batch = "batch_tg_003"
        mark_tg_notified(batch, "-100333444555", chunks=2)
        import importlib
        import _state
        importlib.reload(_state)
        from _state import is_tg_notified
        self.assertTrue(is_tg_notified(batch, "-100333444555"))

    # ── Test 4: Repo update dedup ────────────────────────────────────────────

    def test_no_repo_updated_initially(self):
        from _state import is_repo_updated
        self.assertFalse(is_repo_updated("batch_repo_001", "book"))

    def test_mark_repo_updated(self):
        from _state import mark_repo_updated, is_repo_updated
        batch = "batch_repo_002"
        mark_repo_updated(batch, "book")
        self.assertTrue(is_repo_updated(batch, "book"))
        self.assertFalse(is_repo_updated(batch, "movies"))
        mark_repo_updated(batch, "movies")
        self.assertTrue(is_repo_updated(batch, "movies"))
        self.assertTrue(is_repo_updated(batch, "book"))

    def test_repo_updated_persists_after_reload(self):
        from _state import mark_repo_updated
        batch = "batch_repo_003"
        mark_repo_updated(batch, "tools")
        import importlib
        import _state
        importlib.reload(_state)
        from _state import is_repo_updated
        self.assertTrue(is_repo_updated(batch, "tools"))
        self.assertFalse(is_repo_updated(batch, "games"))


if __name__ == "__main__":
    # force UTF-8 output
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")

    print(f"Batch states dir: {BATCH_DIR}")
    print(f"Clearing and re-creating: {BATCH_DIR}")
    wipe_states()
    print()

    # run tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestIdempotency)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # summary
    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ ALL {result.testsRun} TESTS PASSED")
    else:
        print(f"❌ {len(result.failures)} FAILURES, {len(result.errors)} ERRORS")

    # show generated state files
    print()
    print("Generated state files:")
    for p in sorted(BATCH_DIR.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(BATCH_DIR)}  ({len(p.read_bytes())} bytes)")
            if p.suffix == ".json":
                data = json.loads(p.read_text(encoding="utf-8"))
                print(f"    -> {json.dumps(data, ensure_ascii=False)}")
