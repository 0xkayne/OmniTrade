---
name: ontos-attention-kv
description: >
  Analyze attention architecture and KV cache management for Ontos model integration. Use for MHA/GQA/MQA/MLA/hybrid/SWA/sparse attention, compressor/indexer behavior, O projection structure, RoPE/windowing, KV cache groups, prefix caching, and attention/KV KP checks.
---

# Ontos Attention And KV

Own attention structure, attention execution requirements, and KV cache grouping.

For full attention/KV decision trees, formulas, and examples, read `../../references/details/attention-kv-details.md`.

## KP Focus

Read matching KP files from `../../references/integration_keypoints/`:

- Attention: KP-0006 through KP-0012, KP-0015, KP-0017, KP-0019, KP-0020, KP-0021, KP-0022, KP-0026, KP-0033, KP-0038, KP-0042, KP-0045.
- KV cache: KP-0001 through KP-0005, KP-0013, KP-0046.
- Shape and op verification when attention produces ops: KP-0027, KP-0028, KP-0048, KP-0050, KP-0051.

## Attention Checklist

1. Classify KV projection path: MHA, GQA, MQA, MLA, hybrid, SWA alternating, linear/SSM, or novel.
2. Distinguish MLA from MQA with RoPE split using `kv_lora_rank`, not just `qk_nope_head_dim`.
3. Identify per-layer attention variation from `compress_ratios`, sliding window arrays, or model-specific patterns.
4. Analyze compressed attention strategy: dense compressed, sparse top-k, overlap, dual compressor/indexer.
5. Analyze O projection: standard, grouped low-rank, or novel.
6. Overlay vLLM dossier fusion, kernel, parallel stream, and config rewrite evidence.
7. Decide whether a new AttentionModel or attention backend is needed.

## KV Checklist

1. Group layers by KV cache structure, growth rate, dtype, and block-size requirements.
2. Track SWA bounded groups separately from persistent full/compressed groups.
3. Evaluate prefix caching per group; do not globally reject all groups just because one group is rolling-window.
4. Align KV cache dtype and block size with vLLM, especially hard asserts.

## Output

Return:

- Attention classification and novel branches.
- Per-layer attention pattern summary.
- Required attention model/backend changes.
- KV cache group table.
- KP hits and validation commands.
- Op/profiling implications to pass to `ontos-profiling-plan`.
