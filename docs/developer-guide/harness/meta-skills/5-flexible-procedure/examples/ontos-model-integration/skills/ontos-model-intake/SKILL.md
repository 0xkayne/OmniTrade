---
name: ontos-model-intake
description: >
  Validate input artifacts and extract the initial feature vector for a Ontos model integration. Use at the start of adding/integrating a new model, when comparing against an existing base model, or when the user provides config.json/model.py/kernel.py/model card inputs for Ontos.
---

# Ontos Model Intake

Own input validation, feature extraction, parameter sanity checks, similarity scoring, and novelty detection. Stop after producing the intake packet; do not plan code changes here.

For the full extraction commands and parameter formulas, read `../../references/details/intake-details.md`.

## Inputs

Required:

- `config.json` or equivalent model config.
- Reference `model.py` or `modeling_*.py`.

Optional:

- `kernel.py`, model card, paper/blog, vLLM registry name, base model for incremental mode.

## Output

Produce an intake packet with:

- Artifact inventory and parse status.
- Field naming style: HuggingFace style, custom native style, or mixed.
- Main model/decoder/attention/FFN class names found in reference code.
- Feature vector:
  - `attention_type`, `ffn_type`, `kv_cache_type`, `quant_type`, `residual_type`, `special_features`
  - `num_q_heads`, `num_kv_heads`, `kv_lora_rank`, `head_dim`, `compress_ratios`
  - `n_routed_experts`, `n_shared_experts`, `expert_dtype`
  - `hc_mult`, `sliding_window`, `o_groups`, `o_lora_rank`
  - `has_sim_quant`, `scale_dtype`
- Similarity report against existing Ontos model configs.
- Novelty report: known vs novel dimensions and why.
- Parameter-count sanity check with gaps called out.

## Procedure

1. Parse `config.json` with structured JSON tooling.
2. Extract known fields using aliases for common HuggingFace and native configs.
3. Inspect reference code for class names, projection names, MoE/gate/experts, compressor/indexer, HC/MTP, and quantization calls.
4. Build the feature vector. Use `Novel` or `Other` explicitly when the model does not fit known categories.
5. Compare against integrated model configs in the current Ontos repo when available. Report the closest model and differing dimensions.
6. Compute parameter estimates from the feature vector. Mark formulas as approximate when an architecture dimension is novel or incomplete.

## Handoff

Pass the intake packet to:

- `ontos-vllm-dossier` for serving implementation evidence.
- Dimension skills for KP matching and plan generation.
