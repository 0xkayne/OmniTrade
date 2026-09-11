# Split Skill Map

This plugin decomposes the legacy `model-integrator` skill into smaller active skills.

| Active skill | Owns | Main legacy sections |
|---|---|---|
| `ontos-model-integrator` | Orchestration and handoff | Overview, execution flow, DAG |
| `ontos-model-intake` | Input validation, feature vector, similarity, novelty, parameter sanity | Step -1, Step 0, Step 0.5, Step 0.X |
| `ontos-vllm-dossier` | vLLM serving implementation analysis | Step 0.V |
| `ontos-identity-config` | Identity, config, ModelConfig, catalog/model path | Dimension 1 plus config KPs |
| `ontos-attention-kv` | Attention and KV cache | Dimensions 2 and 4 |
| `ontos-compute-dimensions` | FFN/MoE, quantization, residual, decode acceleration | Dimensions 3, 5, 6, 7 |
| `ontos-profiling-plan` | Op list, profiling matrix, execution semantics | Dimension 8 and plan Section 2 |
| `ontos-plan-validator` | Completeness gates and plan audit | Step 8.7 and validation KPs |
| `ontos-kp-manager` | KP capture, query, export, and split-skill optimization | Legacy kp-manager with updated target model |

Legacy copies are stored under `../../legacy/` for comparison and rollback. They are not the preferred active workflow.

For detailed old decision trees and output templates, use the extracted files under `../../references/details/`; `../../references/detailed-section-index.md` maps each active skill to its detail file.
