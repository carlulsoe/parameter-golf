import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
controller_mod = importlib.import_module("autoresearch.run_pgolf_experiment")


class GracefulHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.repo_dir = self.root / "repo"
        self.repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_dir, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
        )
        (self.repo_dir / "train_gpt.py").write_text("print('ok')\n", encoding="utf-8")
        subprocess.run(["git", "add", "train_gpt.py"], cwd=self.repo_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        self.base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self.trace_root = self.repo_dir / "controller_state" / "autoresearch"
        self.config = self._make_config(queue_depth=2)
        self.controller = controller_mod.PgolfController(self.config)
        self.addCleanup(self.controller.close)

    def _make_config(self, *, queue_depth: int) -> controller_mod.Config:
        return controller_mod.Config(
            proposer_model="gpt-test",
            pre_review_model="gpt-test",
            post_review_model="gpt-test",
            execution_mode="local",
            tag="pgolf",
            deadline=None,
            max_pre_review_rounds=3,
            repo_dir=self.repo_dir,
            data_path="/tmp/data",
            tokenizer_path="/tmp/tokenizer",
            vocab_size=1024,
            nproc_per_node=1,
            max_wallclock_seconds=600,
            val_loss_every=0,
            iterations=20000,
            remote_host="",
            remote_port=22,
            remote_repo_dir="/tmp/remote",
            remote_branch="runpod-autoresearch",
            push_remote="origin",
            remote_fetch_remote="origin",
            remote_torchrun="torchrun",
            remote_identity="",
            remote_force_tty=False,
            local_torchrun="torchrun",
            base_extra_env_text="",
            base_extra_env_pairs=[],
            results_file=self.repo_dir / "results.tsv",
            reviews_file=self.repo_dir / "reviews.tsv",
            harness_log=self.repo_dir / "logs" / "test.log",
            proposer_protocol_file=self.repo_dir / "autoresearch" / "pgolf_autoresearch_prompt.md",
            pre_review_protocol_file=self.repo_dir / "autoresearch" / "pgolf_pre_review_prompt.md",
            post_review_protocol_file=self.repo_dir / "autoresearch" / "pgolf_review_prompt.md",
            trace_root=self.trace_root,
            history_dir=self.trace_root / "history",
            candidates_dir=self.trace_root / "candidates",
            runs_dir=self.trace_root / "runs",
            prep_clones_dir=self.trace_root / "prep_clones",
            remote_log_dir=self.repo_dir / "remote_logs",
            queue_file=self.trace_root / "ready_queue.json",
            prep_queue_depth=queue_depth,
            prep_worker_count=1,
            prep_poll_seconds=1.0,
            infrastructure_retry_schedule=(3.0, 30.0, 300.0),
            codex_binary="codex",
        )

    def _create_candidate(
        self,
        number: int,
        *,
        status: str,
        approved_at: str = "2026-03-20T00:00:00+00:00",
    ) -> Path:
        candidate_id = f"candidate_{number:04d}"
        candidate_dir = self.config.candidates_dir / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        approved_patch = candidate_dir / "approved.patch"
        approved_patch.write_text("patch\n", encoding="utf-8")
        approved_spec = candidate_dir / "approved.json"
        approved_spec.write_text(
            json.dumps(
                {
                    "IDEA": f"idea {number}",
                    "HYPOTHESIS": "test hypothesis",
                    "EXPECTED_SIGNALS": "test signals",
                    "NOTES": "test notes",
                    "EXTRA_ENV": "",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path = candidate_dir / "manifest.json"
        controller_mod.write_json(
            manifest_path,
            {
                "candidate_id": candidate_id,
                "base_commit": self.base_commit,
                "created_at": approved_at,
                "status": status,
                "approved_at": approved_at,
                "approved_round": 1,
                "approved_patch": str(approved_patch),
                "approved_spec": str(approved_spec),
            },
        )
        return manifest_path

    def _queue_ids(self) -> list[str]:
        payload = json.loads(self.config.queue_file.read_text(encoding="utf-8"))
        return [item["candidate_id"] for item in payload["items"]]

    def test_enqueue_and_dequeue_persist_queue_file(self) -> None:
        manifest_path = self._create_candidate(1, status="approved")
        candidate = self.controller._load_prepared_candidate_from_manifest(manifest_path)
        assert candidate is not None

        self.assertTrue(self.controller._enqueue_ready_candidate(candidate, emit_history=False))
        self.assertEqual(self._queue_ids(), ["candidate_0001"])
        self.assertEqual(
            controller_mod.read_json_object(manifest_path)["status"],
            "queued",
        )

        dequeued = self.controller._dequeue_ready_candidate()
        self.assertIsNotNone(dequeued)
        assert dequeued is not None
        self.assertEqual(dequeued.candidate_id, "candidate_0001")
        self.assertEqual(self._queue_ids(), [])
        self.assertEqual(
            controller_mod.read_json_object(manifest_path)["status"],
            "dequeued",
        )

    def test_restore_queue_rehydrates_durable_queue_and_approved_backlog(self) -> None:
        manifest_a = self._create_candidate(
            1,
            status="queued",
            approved_at="2026-03-20T00:00:00+00:00",
        )
        self._create_candidate(
            2,
            status="approved",
            approved_at="2026-03-20T00:01:00+00:00",
        )
        controller_mod.write_json(
            self.config.queue_file,
            {
                "version": 1,
                "items": [
                    {
                        "candidate_id": "candidate_0001",
                        "manifest_path": str(manifest_a),
                    }
                ],
            },
        )

        self.controller._restore_ready_queue()

        self.assertEqual(self._queue_ids(), ["candidate_0001", "candidate_0002"])
        self.assertEqual(self.controller.ready_queue.qsize(), 2)
        manifest_b = self.config.candidates_dir / "candidate_0002" / "manifest.json"
        self.assertEqual(
            controller_mod.read_json_object(manifest_b)["status"],
            "queued",
        )

    def test_restore_queue_recovers_running_candidate(self) -> None:
        manifest_path = self._create_candidate(3, status="running")

        self.controller._restore_ready_queue()

        self.assertEqual(self._queue_ids(), ["candidate_0003"])
        self.assertEqual(
            controller_mod.read_json_object(manifest_path)["status"],
            "queued",
        )

    def test_restore_queue_recovers_from_partial_queue_file(self) -> None:
        self._create_candidate(4, status="approved")
        self.config.queue_file.write_text('{', encoding="utf-8")

        self.controller._restore_ready_queue()

        self.assertEqual(self._queue_ids(), ["candidate_0004"])


if __name__ == "__main__":
    unittest.main()
