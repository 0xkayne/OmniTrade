---
name: ontos-identity-config
description: >
  Analyze Ontos model identity, config registration, config inheritance, dtype/model-path constraints, and config-field synchronization. Use during model integration after intake, especially for ModelConfig, catalog.yaml, profiling ModelConfig, model name mapping, or KP identity/config checks.
---

# Ontos Identity And Config

Own model registration and config correctness. Use the intake packet, vLLM dossier, and KP files.

For the full registration decision tree and verification snippets, read `../../references/details/identity-config-details.md`.

## KP Focus

Read matching KP files from `../../references/integration_keypoints/` for these tags and IDs:

- `identity`: KP-0033, KP-0035, KP-0041, KP-0046.
- Cross-cutting config/profiling: KP-0034, KP-0057 when config reconstruction or profiling config is involved.

## Analysis Checklist

1. Decide the canonical model identifier:
   - Prefer `config.json` `model_type` for HuggingFace style configs.
   - For native configs, derive from user-provided model name, repo path, or main class name.
2. Compare against existing Ontos `BaseModelConfig` subclasses.
3. If reusing a parent config, audit every inherited field against official model specs and vLLM rewrites.
4. Verify `is_mla_model()`, sparse backend flags, MoE flags, head dimension, and dtype flags.
5. Check catalog/model path requirements from vLLM.
6. Check profiling `ModelConfig` can accept all new BaseModelConfig fields.
7. Preserve Enum/post-init semantics when reconstructing configs from YAML/JSON.

## Output

Return:

- Registration decision: new subclass, reused subclass, or modified subclass.
- Required fields and overrides.
- vLLM naming/config rewrite mapping.
- KP hits and required validations.
- Exact files likely affected.
