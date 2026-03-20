Run exactly one Parameter Golf proposer iteration in the current repository clone.

Context to read before acting:
- `gpt-pro.md`
- `ideas/README.md`
- `ideas_wild/README.md`
- `results.tsv` if it exists
- `reviews.tsv` if it exists
- `controller_state/autoresearch/history/summary.md` if it exists
- `controller_state/autoresearch/history/ledger.jsonl` if it exists
- the latest relevant log under `logs/`

Goal:
- Improve final `final_int8_zlib_roundtrip_exact val_bpb` on the FineWeb validation set.
- Lower `val_bpb` is better.
- Prioritize ideas that can improve this 1xA40 proxy in a trustworthy way, with extra weight on mechanisms that are plausibly transferable from stronger 8xH100 record runs. Deprioritize narrow one-off export bookkeeping tweaks unless repo evidence strongly supports them.

Protocol:
1. Inspect `git status`, recent commits, `results.tsv`, and recent logs.
2. Inspect the recent autoresearch history so you do not repeat obviously bad ideas and so you can build on wins.
3. Choose exactly one focused idea or ablation to test.
4. Make only the code changes needed for that one idea. You may edit `train_gpt.py` directly.
5. Do not edit any tracked file other than `train_gpt.py`.
6. Write `controller_state/current_candidate.json` as a JSON object with exactly these string fields:
   - `IDEA`
   - `HYPOTHESIS`
   - `EXPECTED_SIGNALS`
   - `NOTES`
   - `EXTRA_ENV`
7. `HYPOTHESIS` should say why the change should help.
8. `EXPECTED_SIGNALS` should say what metrics or log changes would support or falsify the hypothesis.
9. `EXTRA_ENV` must be a single-line space-separated list of additional `KEY=VALUE` pairs for this run, for example `TRAIN_SEQ_LEN=512 EVAL_SEQ_LEN=1024`.
10. Make exactly one git commit for your experiment before stopping so the controller can export it as a single patch.
11. Do not commit `controller_state/current_candidate.json`. Leave it as an untracked file.
12. If you accidentally stage `controller_state/current_candidate.json`, unstage it before the final commit.
13. Do not run training yourself. The controller will run the exact experiment remotely on the GPU box if the pre-reviewer approves it.
14. Stop after one completed proposer iteration.

Rules:
- Do not ask for confirmation.
- Do not delete or redownload the dataset.
- Do not change the tokenizer or dataset export path unless that is the explicit experiment.
- Prefer bounded changes that can be evaluated in one run.
- Prefer ideas from these categories:
  - evaluation correctness or richer-context evaluation that keeps comparisons trustworthy, for example sliding-window evaluation or partial-window accounting fixes
  - training schedule and optimizer changes that plausibly reduce quantization damage, for example lower learning rates, longer warmdown, momentum or weight-decay tuning, or batch/update tradeoffs
  - architecture and export co-design that is still realistic on this proxy, for example modest depth or width changes, selective fp16 on especially sensitive tensors, or mixed-precision layer plans that improve the final compressed artifact
  - training-side changes that make the final int8/zlib metric better for causal reasons, not just post-hoc reporting changes
- Deprioritize these categories unless recent evidence in the repo strongly supports them:
  - narrow export-only audits, allowlist reshuffles, or selector/bookkeeping tweaks with no strong causal story for transferability
  - patches whose main effect is to exploit an evaluation artifact rather than improve a trustworthy final comparison
  - large bundles of loosely related ideas that make attribution hard
- Use the upstream 8xH100 records as idea sources, not as a target for exact hyperparameter copying. Adapt them to what can plausibly teach us something in a 1xA40 10-minute run.
- In `HYPOTHESIS`, explain both why the idea should help this proxy and why the mechanism might transfer to the full challenge regime.
- Keep the code change self-contained and cherry-pick friendly. The controller may apply it onto a slightly newer reviewed state after other runs finish.
- Keep the repo runnable after the iteration.
- Do not update `results.tsv` or `reviews.tsv` yourself.
- Do not revert the experiment commit yourself. The controller and later reviewers handle queueing and keep/revert decisions.
- The repo clone must be left with a clean working tree except for the committed experiment change and the untracked `controller_state/current_candidate.json`.
- Stop after one completed proposer iteration.
