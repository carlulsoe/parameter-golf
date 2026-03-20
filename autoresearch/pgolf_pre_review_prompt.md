Review exactly one proposed Parameter Golf patch before it is allowed onto the remote GPU queue.

Context to read before acting:
- the candidate patch file
- the candidate rationale/env file
- `results.tsv`
- `reviews.tsv`
- `controller_state/autoresearch/history/summary.md` if it exists

Goal:
- Catch weak, buggy, untrustworthy, or invalid changes before they spend GPU time.
- Approve only candidates that are clear enough to test and likely to improve the proxy in a trustworthy way, especially when they also teach us something transferable about the final challenge regime.

Protocol:
1. Read the patch and the candidate rationale carefully.
2. Check whether the hypothesis and expected signals actually match the code change.
3. Review with a code-review mindset:
   - correctness
   - invalid comparisons
   - hidden confounders
   - missing accounting
   - reasons the claimed win would not be trustworthy
   - whether this is a meaningful test of a transferable mechanism versus narrow local bookkeeping churn
4. Decide one of:
   - `approve`
   - `revise`
5. Write the decision file as a JSON object with exactly these string fields:
   - `DECISION`
   - `SUMMARY`
   - `FINDINGS`
   - `FEEDBACK`
6. If `revise`, `FEEDBACK` must contain concrete instructions for the next proposer round.
7. Stop after one completed pre-review.

Rules:
- Do not edit the repository yourself.
- Do not run training.
- Be strict about trustworthiness.
- Give extra credit to bounded tests of mechanisms already supported by stronger upstream records, for example evaluation-context fixes, lower-LR or warmdown ideas, optimizer tuning, or architecture/export co-design that could survive regime transfer.
- Prefer `approve` for a clean, focused, trustworthy test even if the expected gain is uncertain.
- Prefer `revise` over approving a vague, weakly justified, buggy, or confounded patch.
- Prefer `revise` for narrow export-only bookkeeping, allowlist, or selector churn unless the candidate makes a strong causal case that it should improve the final metric.
- Do not send a candidate back for another round only because the writeup is imperfect if the actual experiment is clear, safe, and high-signal.
