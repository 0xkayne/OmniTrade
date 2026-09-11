# DeepSeek V4 Profiling Op 完整分析

> 分析日期: 2026-05-14
> 基于: `deepseek_v4_implementation_plan.md`, `deepseek_v4_architecture.json`, 现有 profiling 系统

---

## 一、V4 模型结构总览

### 基本参数
- **层数**: 61 (0-60)
- **hidden_size**: 7168
- **Attention**: MQA (num_q_heads=128, num_kv_heads=1, head_dim=512)
- **KV cache per token**: head_dim = 512 (vs V3 MLA: kv_lora_rank + rope = 576)
- **MoE**: 384 routed experts + 1 shared, topk=6, FP4 expert weights
- **compress_ratios**: [128,128,4,128,4,...,4,0] — 每层不同
- **HC (Hyper-Connections)**: hc_mult=4, 每层 4 个 HC ops

### 每层 compress_ratio 分布

| Ratio | 含义 | 层数 | 层索引 |
|-------|------|------|--------|
| 0 | 纯 SWA (window=128) | 1 | 60 |
| 4 | SWA + Compressed(4:1) + TopK=1024 sparse | ~30 | 2,4,6,...,58 (偶数, 0-based) |
| 128 | SWA + Compressed(128:1) + dense | ~30 | 0,1,3,5,...,59 (奇数+前两层) |

### V4 vs V2/V3 核心差异

| 维度 | V2/V3 MLA | V4 MQA |
|------|-----------|--------|
| KV path | kv_a(7168→576) → kv_b(512→heads×dim) | wkv(7168→512) 直投，单 KV head |
| KV cache/tok | kv_lora_rank + rope = 576 | head_dim = 512 |
| O projection | o_proj(heads×v_dim → hidden) | wo_a(grouped) + wo_b 分组低秩 |
| Compressor | 无 | compressor_wkv + wgate + gating |
| 残差 | Add | HCPre + HCPost (Sinkhorn) |
| Expert quant | FP8/W4A8 | FP4 (expert weights only) |
| Indexer (sparse) | V3.2: index_topk=2048 | V4: index_topk=1024 |

---

## 二、每层 Forward Pass 完整算子序列

```
1.  HCPre-Attention           ← V4 NEW (Sinkhorn mixing, 4→1)
2.  input_layernorm           ← RMSNorm(7168)
3.  wq_a                      ← Linear(7168, 1536)       [Q low-rank A]
4.  q_a_norm                  ← RMSNorm
5.  wq_b                      ← Linear(1536, 65536)      [Q low-rank B]
6.  wkv                       ← Linear(7168, 512)        [MQA direct KV]
7.  kv_norm                   ← RMSNorm
8.  rope                      ← RoPE(64 dim)
9.  compressor_wkv            ← Linear(7168, 512)        [仅 ratio>0 层]
10. compressor_wgate          ← Linear(7168, 512)        [仅 ratio>0 层]
11. compressor_gating         ← Sigmoid + multiply       [仅 ratio>0 层]
12. indexer_wq_b              ← Linear                   [仅 C4 层]
13. indexer_weights_proj      ← Linear                   [仅 C4 层]
14. indexer_k_norm            ← RMSNorm                  [仅 C4 层]
15. indexer_rope              ← RoPE                     [仅 C4 层]
16. attn_prefill / attn_decode ← Attention kernel         [按 ratio 分发]
17. attn_kv_cache_save        ← KV Cache write
18. wo_a                      ← Linear (grouped)         [V4 grouped O-proj A]
19. wo_b                      ← Linear                   [V4 grouped O-proj B]
20. HCPost-Attention          ← V4 NEW (expand 1→4)
21. all_reduce                ← [仅 TP>1]
22. post_attention_layernorm  ← RMSNorm(7168)
23. HCPre-FFN                 ← V4 NEW
24. [FFN 或 MoE block]        ← 见下方
25. HCPost-FFN                ← V4 NEW
26. all_reduce                ← [仅 TP>1]
```

### MoE Block (layers 3-60)

```
24a. share_expert_up_proj      ← Linear
24b. share_expert_down_proj    ← Linear
24c. share_expert_act          ← SiLU
24d. moe_gate                  ← Linear(7168, 384)
24e. moe_topk                  ← TopK(6 from 384)
24f. {backend}_dispatch        ← All-to-All [仅 DP>1]
24g. nocomm_prepare            ← [非 NONE backend]
24h. fused_experts             ← FP4 GEMM (384 experts × 3072 hidden)
24i. nocomm_finalize           ← [非 NONE/DEEPEPLL]
24j. {backend}_combine         ← All-to-All [仅 DP>1]
```

### Dense FFN (layers 0-2)

```
24a. mlp_up_proj               ← Linear
24b. mlp_down_proj             ← Linear
24c. mlp_act                   ← SiLU
```

---

## 三、Profiling 分类与策略

### Category A: Attention Kernels — 需要新 Profiling Backend

这是最关键的 profiling 需求。V4 有 3 种 attention 模式，每种有 prefill/decode。

| ID | Op | 描述 | KV Cache Size | Profiling 策略 |
|----|-----|------|---------------|---------------|
| A1 | V4 SWA Prefill | ratio=0, window=128, MQA, head_dim=512 | min(seq, 128) × 512 | 新 backend 或复用 sliding_window (调整 head_dim) |
| A2 | V4 SWA Decode | ratio=0, decode | min(seq, 128) × 512 | 同上 |
| A3 | V4 C4 Hybrid Prefill | ratio=4, SWA+C4(4:1)+TopK=1024 | (128 + seq/4) × 512, sparse TopK | **需新 backend: SPARSE_ATTN_C4** |
| A4 | V4 C4 Hybrid Decode | ratio=4, decode, kv clamp to topk | min(kv, 1024) tokens | **需新 backend: SPARSE_ATTN_C4** |
| A5 | V4 C128 Hybrid Prefill | ratio=128, SWA+C128(128:1)+dense | (128 + seq/128) × 512 | 新 backend 或复用 MLA dense (调整 head_dim) |
| A6 | V4 C128 Hybrid Decode | ratio=128, decode | (128 + seq/128) × 512 | 同上 |

**关键约束:**
- head_dim=512 超出标准 FlashAttention 支持 (通常 ≤ 256)
- 需要支持 MQA (128 Q heads, 1 KV head)
- C4 sparse 使用自定义 TileLang `sparse_attn` kernel (尚未公开)
- Indexer timing 可沿用 `SparseIndexerTimingMixin` 模式融入 ATTN timer

**早期近似方案:**
- C4 → 复用 V3.2 `FLASHMLA_SPARSE` / `FLASHINFER_MLA_SPARSE` data
- C128 → 复用 V2/V3 `FLASHMLA` / `FLASHINFER_MLA` data (调整 head_dim)
- SWA → 复用 sliding window data (调整 head_dim)

### Category B: Linear Ops — 完全复用现有 Profiling

所有 Linear ops 复用现有 `LinearTimer` 基础设施。仅需提供正确的 shapes。

| ID | Op | Shape | 备注 |
|----|-----|-------|------|
| B1 | wq_a | (7168, 1536) | Q low-rank A, 复用 |
| B2 | wq_b | (1536, 65536) | Q low-rank B, 可能 TP split |
| B3 | wkv | (7168, 512) | MQA 直投, 复用 |
| B4 | wo_a | grouped(4096, 16384) | 分组低秩, 新 shape |
| B5 | wo_b | (16384, 7168) | 复用 |
| B6 | compressor_wkv | (7168, 512) | 仅 ratio>0 层 |
| B7 | compressor_wgate | (7168, 512) | 仅 ratio>0 层 |
| B8 | indexer_wq_b | (?) | 仅 C4 层, 复用 |
| B9 | indexer_weights_proj | (?) | 仅 C4 层, 复用 |
| B10 | share_expert_up_proj | (7168, moe_hidden) | 复用 |
| B11 | share_expert_down_proj | (moe_hidden, 7168) | 复用 |
| B12 | moe_gate | (7168, 384) | 复用 |
| B13 | mlp_up_proj | (7168, mlp_hidden) | Layers 0-2, 复用 |
| B14 | mlp_down_proj | (mlp_hidden, 7168) | Layers 0-2, 复用 |
| B15 | lm_head | (7168, 129280) | 复用 LmHead |
| B16 | embedding | (129280, 7168) | 复用 Embedding |

### Category C: MoE/FFN Ops — 复用 + FP4 调整

| ID | Op | Profiling |
|----|-----|-----------|
| C1 | MoETopK (topk=6) | 复用现有 |
| C2 | FusedExperts (FP4) | **需调整**: FP4 量化因子 0.5× 或实际 FP4 GEMM profiling |
| C3 | NoCommPrepare | 复用现有 |
| C4 | NoCommFinalize | 复用现有 |
| C5 | MoEDispatch (all2all) | 复用现有 (naive/deepepll/deepepht/agrs) |
| C6 | MoECombine (all2all) | 复用现有 |

**FP4 FusedExperts 处理方案:**
- 方案1: 复用 BF16/FP8 FusedExperts data × 0.5 缩放因子
- 方案2: 实际 FP4 GEMM profiling (需要 FP4 kernel 支持)
- 推荐: 先用方案1 快速验证，后续用方案2 精确化

### Category D: Element-wise Ops — 完全复用现有

| ID | Op | Profiling |
|----|-----|-----------|
| D1 | RMSNorm (×5/层) | 复用 |
| D2 | RoPE (×1-2/层) | 复用 |
| D3 | SiLU / Sigmoid (gating) | 复用 |
| D4 | Add (residual) | 复用 |

### Category E: HC Ops — 新增，可估算

| ID | Op | 描述 | Profiling |
|----|-----|------|-----------|
| E1 | HCPre-Attention | Sinkhorn(20 iters), reduce 4→1 | 估算: element-wise bandwidth bound |
| E2 | HCPost-Attention | Weighted expand 1→4 | 估算: element-wise bandwidth bound |
| E3 | HCPre-FFN | 同 E1 | 估算 |
| E4 | HCPost-FFN | 同 E2 | 估算 |

**估算方法:**
- HCPre: Linear(hc_mult×dim, (2+hc_mult)×hc_mult) + 20 iters Sinkhorn
- 实际是 memory-bandwidth bound, 可用 `ElementWiseWrapperTimer` 测量
- 或直接估算为常量开销 (Sinkhorn 20 iters 在 dim=7168 上很快)

### Category F: Communication Ops — 完全复用

| ID | Op | Profiling |
|----|-----|-----------|
| F1 | All-Reduce | 复用 |
| F2 | All-to-All (dispatch/combine) | 复用 |

### Category G: 非层组件 — 复用

| ID | Op | Profiling |
|----|-----|-----------|
| G1 | Token Embedding | 复用 |
| G2 | LM Head | 复用 |
| G3 | MTP Block | 复用 layer ops (compress_ratio=0, SWA) |

---

## 四、总结: Profiling 工作量评估

### 必须新做的 (3 项)

1. **V4 Attention Backend** — 3 ratio 变体 × prefill/decode = 6 个 profiling 场景
   - 最大工作量项
   - 需要决定: 新 backend vs 复用现有 + 近似

2. **FP4 FusedExperts** — 量化因子调整
   - 工作量小，但影响 MoE 时间预测精度

3. **HC Ops** — 估算或 profiling
   - 可简单估算，不阻塞主线

### 可直接复用的 (无需新工作)

- 所有 Linear ops (16 个不同 shape)
- 所有 Element-wise ops (RMSNorm, RoPE, SiLU, Add)
- 所有 MoE structural ops (gate, topk, prepare, finalize)
- 所有 Communication ops (all-reduce, all2all)
- Embedding, LM Head

### 早期近似方案 (快速验证路径)

如果不等新 attention backend profiling, 可:
1. C4 → 复用 V3.2 `FLASHMLA_SPARSE` CSV (最接近的已有数据)
2. C128 → 复用 V2/V3 `FLASHMLA` CSV (head_dim 调整)
3. SWA → 复用 sliding window CSV (head_dim 调整)
4. HC → 常量估算
5. FP4 → 0.5× 缩放因子
6. **即可跑通 Phase 1-5 的完整 simulate 流程**
