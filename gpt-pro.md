The baseline is a 9-layer, 512-dim GPT with 8 attention heads / 4 KV heads, 2× ReLU² MLPs, tied embeddings by default, sequence length 1024, Muon on matrix parameters, Adam on embeddings/scalars, and a final per-row int8 + zlib export. The challenge score is `val_bpb`; the counted artifact is code bytes plus compressed model bytes under 16,000,000 bytes; evaluation must finish under 10 minutes on 8×H100; and the README explicitly allows evaluation at any sequence length while warning that tokenizer changes will be scrutinized carefully.  

My read: the biggest upside here is not “tune LR a bit.” It is exploiting evaluation freedom, stopping the unnecessary byte-saving tricks that barely save bytes in this exact model, optimizing the quantized artifact rather than the bf16 model, and reallocating parameters away from low-value places. The script already exposes `TIE_EMBEDDINGS`, `NUM_KV_HEADS`, `TRAIN_SEQ_LEN`, separate optimizer groups, and a simple export path, so a lot of the best ideas are incremental rather than a rewrite. 

The first five things I would actually run are:

1. streamed 4k–8k evaluation with causal KV cache and RoPE scaling,
2. a strictly causal cache-LM / n-gram mixture at eval,
3. untie the output head and cut KV heads to 1,
4. EMA + short fake-quant tail before export,
5. 512-train / long-eval or a 512→1024 curriculum, then sweep a slightly larger model.

## Evaluation and test-time compute

**1. Streaming long-context evaluation with decoupled `EVAL_SEQ_LEN`, ideally chunked with KV cache.**
Why: the current `eval_val` hard-resets context every `TRAIN_SEQ_LEN`, even though the challenge allows evaluation at any sequence length. That is direct score leakage: you are throwing away usable prefix information on every block boundary. A streamed evaluator with a long causal memory is the cleanest “free” gain in this codebase. Add NTK/Yarn-style RoPE scaling once eval context exceeds train context. Speed: eval gets slower if done naively, but chunked KV-cache eval keeps it reasonable; training unchanged. Size: essentially neutral, just code bytes. Complexity/risk: low-moderate; the only real hazard is exact causal accounting across rank boundaries. Fit: extremely clean inside `eval_val` plus a logits-returning forward path.  

**2. Add a strictly causal cache model on top of the neural model at evaluation.**
Why: small LMs leave obvious gains on local repetition and document-specific vocabulary. A simple interpolation `p = λ p_model + (1-λ) p_cache` with unigram/bigram/trigram counts or a neural cache over recent hidden states can move bpb more than many architecture tweaks. This is classic compression-competition thinking and matches the challenge’s openness to test-time compute. Speed: moderate eval slowdown only. Size: almost free in model bytes; only code grows. Complexity/risk: moderate-high because you need a logits path, careful causal bookkeeping, and tuning of the interpolation rule. Fit: doable in this script once forward can optionally return logits.

**15. Tiny online adaptation of output biases during evaluation.**
Why: per-document or per-stream unigram shifts can help a lot on web text. Speed: eval slows somewhat. Size: tiny. Complexity/risk: moderate-high. Fit: easy. Validity: borderline. A lightweight causal cache is probably fine; actual gradient updates on validation tokens are where I would expect “not in the spirit” pushback. I would only treat this as a late-stage experiment, not a first-line submission strategy. 

## Architecture changes

**3. Untie the output head.**
Why: in this exact script, tying is buying very little. The vocab is only 1024, so untying adds just `1024 × 512 = 524,288` weights, which is small relative to the transformer blocks. Separate input and output embeddings usually help more than that costs. Speed: basically unchanged. Size: slightly larger. Complexity/risk: trivial. Fit: already implemented; this is an immediate toggle plus maybe a head-LR sweep. 

**5. Drop to 1–2 KV heads and spend the savings elsewhere.**
Why: 4 KV heads is generous for an 8-head, 512-dim model. Going 4→1 saves about 1.77M weights in this baseline, which is more than 3× the cost of untying the head. My default bet is `NUM_KV_HEADS=1`, then spend the recovered budget on depth, width, or a slightly better MLP. Speed: slightly faster or neutral. Size: smaller unless you reinvest it. Complexity/risk: low. Fit: already parameterized. 

**8. Make the model modestly larger and train it on fewer tokens.**
Why: this baseline is tiny for 8×H100, and the README’s 4-hour run beating it with essentially the same family strongly suggests the 10-minute script is under-optimized more than fundamentally well-sized. I would sweep around slightly larger models rather than assuming 9×512 is the compute-optimal point. Concrete branches: `12×512`, `14×448/480`, or `12×576`, preferably combined with KV-head reduction and an untied head. Speed: slower per step, but you compensate with fewer steps or shorter train context. Size: moderately larger. Complexity/risk: low-moderate. Fit: very clean.  

**9. Pairwise/shared block recurrence with per-layer gates left untied.**
Why: this is exactly the kind of parameter-golf move the challenge is inviting. Tie blocks in pairs or repeat a smaller bank of blocks, but keep per-layer control tensors (`attn_scale`, `mlp_scale`, `resid_mix`, maybe `q_gain`) untied so the repeated block can behave differently at different depths. This buys effective depth per byte. Speed: same if depth stays fixed, slower if you cash savings into more unrolls. Size: much better byte efficiency. Complexity/risk: moderate. Fit: clean enough; the current per-layer control tensors make this much more plausible than in a plain GPT. 

**11. Replace all or some ReLU² MLPs with a parameter-matched SwiGLU/GEGLU.**
Why: gated MLPs are usually better per parameter than plain ReLU². I would not blindly inflate MLPs; I would use a parameter-matched hidden size or only switch the upper layers where semantics matter more. Speed: slightly slower. Size: neutral to slightly larger. Complexity/risk: low-moderate. Fit: easy in `MLP`.

**12. Add a tiny causal depthwise-conv or token-mixing branch in lower layers.**
Why: FineWeb has lots of short-range regularity. A causal depthwise conv costs almost nothing in parameters and can improve local modeling without paying full attention costs. Speed: slightly slower. Size: almost unchanged. Complexity/risk: moderate because it may not help enough to justify the added kernel. Fit: still clean inside `Block`.

## Optimization and training changes

**4. Maintain an EMA / Polyak average and export the averaged weights.**
Why: 10-minute runs are noisy, and EMA often improves validation more than almost any single hyperparameter tweak. It also tends to reduce outliers, which can slightly help compression and quantization. Speed: negligible overhead. Size: no submission-size increase if you only save the EMA copy. Complexity/risk: very low. Fit: one of the cleanest wins available.

**6. Train shorter than you evaluate; ideally use a sequence-length curriculum.**
Why: the baseline pays quadratic attention cost for 1024-token training from step 1, but evaluation can use longer context anyway. A very strong branch is `TRAIN_SEQ_LEN=512` with long streamed eval, or a `512 → 1024` schedule late in training. That either buys more tokens/sec or lets you afford a larger model. Speed: faster training early, slower eval. Size: unchanged. Complexity/risk: moderate because `torch.compile(dynamic=False)` dislikes shape changes; you either do a simple two-stage schedule or precompile both shapes. Fit: still cleanly inside this script. 

**7. Do a compression-aware tail: fake-quant or QAT for the last few hundred steps.**
Why: your leaderboard score is on the quantized round-trip model, not the bf16 training model. Right now the exporter is a post-hoc per-row/per-tensor int8 scheme with percentile clipping. I would simulate exactly that in the last 300–800 steps so the trained weights are optimized for the thing you actually submit. This also opens the door to 6-bit/4-bit or codebook export later. Speed: slight slowdown late only. Size: neutral or smaller if it enables harsher quantization. Complexity/risk: moderate. Fit: very reasonable around the existing quantizer/exporter. 

**10. Replace deterministic sequential streaming with randomized block sampling.**
Why: the current loader is a single token stream that wraps. Once you loop the dataset, you see the same prefix again in the same order. Cheap random shard/block offsets should improve gradient diversity and reduce ordering artifacts. Speed: about the same if you keep the loader simple. Size: unchanged. Complexity/risk: low-moderate. Fit: cleanly in `TokenStream` / `DistributedTokenLoader`. 

## Serialization and compression changes

**13. Replace `torch.save` + pickle metadata with a custom flat packer.**
Why: `torch.save` is wasting bytes on names, Python object structure, and general-purpose serialization. A flat binary format with short tensor IDs, compact shape metadata, and concatenated payload/scale blobs will save real space. That space is better spent on a better model than on serialization overhead. After that, I would try grouped 6-bit or 4-bit quantization on the biggest matrices only, but only after the fake-quant tail. Speed: load time is still trivial at these sizes. Size: better, potentially much better if you push model size upward. Complexity/risk: moderate-high. Fit: yes, though it is more enabling infrastructure than direct bpb.

## Tokenizer and data-interface changes

**14. Sweep tokenizer size upward a bit, but keep it inside SentencePiece unless you have a compelling reason not to.**
Why: tokenizer-agnostic scoring does not mean tokenizer-irrelevant scoring. A 2k or 4k SP vocab may improve the bpb frontier enough to be worth the modest extra embedding/head cost, especially once you untie the head and stop optimizing around 1024 vocab as if embedding bytes dominated. Speed: similar. Size: slightly to moderately larger. Complexity/risk: high; you have to retokenize data and prove `val_bpb` is still computed exactly. Fit: this is the least “inside train_gpt.py” of the serious ideas, and the README explicitly says tokenizer changes will be audited hard.  

## What I would avoid or treat as submission-risky

External-teacher distillation or offline logits from a larger model is the first thing I would flag as spirit-risky. Likewise, any evaluation trick that peeks ahead or effectively trains on future validation tokens is just invalid. Full-blown gradient-based test-time training on the validation set may or may not be technically arguable, but it is the kind of thing that invites a rules fight. The safer ruthless path is causal cache-style adaptation, long-context streamed eval, EMA/QAT, and parameter reallocation. 

My most likely winning branch for this exact codebase is: untie the head, cut to 1 KV head, add EMA, train at 512 then evaluate with streamed 4k–8k context plus RoPE scaling, and layer on a strictly causal cache mixture. After that, I would spend any remaining wallclock/size slack on a slightly larger model rather than on exotic optimizer tuning.
