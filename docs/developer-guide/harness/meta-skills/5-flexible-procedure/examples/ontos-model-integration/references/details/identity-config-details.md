# Identity And Config Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## 维度 1: 模型身份注册

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 1.1 | 模型的标识符是什么？ | config.json `model_type` (HuggingFace) 或目录名/文件名 (自定义) | 见下方决策树 |
| 1.2 | 是否已有同系列已注册模型？ | `get_all_subclasses(BaseModelConfig)` | 遍历查找 |
| 1.3 | config.json 是 HuggingFace 格式还是自定义格式？ | Step -1 风格检测结果 | 决定如何映射到框架的 ModelConfig 字段 |
| 1.V | vLLM Dossier: Config Rewrite Map 中是否有 model_type 相关的重写？ | Dossier D | 影响框架 ModelConfig 的注册名和 catalog.yaml 配置 |

### 决策树

```
config.json 有 model_type 字段? (HuggingFace 标准格式)
  YES → 用 model_type 作为标识符
        model_type 已有匹配的 ModelConfig?
          YES → 复用子类，检查是否需要扩展字段
          NO  → 新建 BaseModelConfig 子类

  NO → 自定义格式 (如 DeepSeek 原生、Qwen 原生)
        从以下来源推断标识符:
          1. 用户提供的模型名称 (如 "deepseek-ai/DeepSeek-V4-Pro")
          2. 文件所在目录名或仓库名
          3. model.py 主类名 (如 Transformer → 推断 deepseek 系列)
        推断的标识符已有匹配的 ModelConfig?
          YES → 复用子类
          NO  → 新建 BaseModelConfig 子类

新建时需处理:
  - 将自定义字段名映射到框架的 ModelConfig 标准字段
    (如 dim → hidden_size, n_layers → num_hidden_layers)
  - 记录映射关系到 ModelConfig 子类注释中

注册后完整性验证:
  is_mla_model() 是否准确? [KP-0033]
    检查: 如果 qk_nope_head_dim IS NOT NONE 且 kv_lora_rank IS NONE
      → 这是 "MQA with RoPE split"，不是 MLA
      → is_mla_model() 必须基于 kv_lora_rank is not None 判定
      → 错误判定会导致模型进入 MLA 代码路径 (错误 backend / wrong KV cache sizing)

  Profiling ModelConfig 是否接受新字段? [KP-0035]
    验证: ModelConfig.from_model_name(model_name) 不崩溃
    如果崩溃 → profiling ModelConfig.__init__ 缺少新增的 BaseModelConfig 字段
      → 修复: 增加 **kwargs 兜底或显式添加参数
```

### 实现路径

文件: `ontos/config/model_config.py`

```python
@dataclass
class NewModelConfig(BaseModelConfig):
    @staticmethod
    def get_name() -> str:
        return "org/model-name"
    # 后续维度分析确定需要的特有字段
```

发现机制: `BaseModelConfig.create_from_name()` 通过 `get_all_subclasses()` 自动发现，无需显式注册。

### 验证

```bash
python -c "
from ontos.config.model_config import BaseModelConfig
cfg = BaseModelConfig.create_from_name('org/model-name')
assert cfg.get_name() == 'org/model-name'
"
```

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0033 | P0 | mla_detection | `qk_nope_head_dim IS NOT NONE AND kv_lora_rank IS NONE` | is_mla_model() 必须基于 kv_lora_rank，不是 RoPE 维度拆分 |
| KP-0035 | P1 | config_field_sync | `新增 BaseModelConfig 字段` | profiling ModelConfig 必须接受所有 BaseModelConfig 字段 (**kwargs 兜底) |
| KP-0041 | P1 | config_inheritance_audit | `新模型 config 继承自已有 ModelConfig 子类` | 继承链中每个字段都必须与官方规格对比，不同的必须显式覆盖；V4 错误继承了 q_lora_rank/routed_scaling_factor/first_k_dense_replace |
| KP-0046 | P0 | kv_cache_dtype_hard_requirement | `model_type == "deepseek_v4"` | V4 要求 FP8 KV cache (vLLM assert `fp8` 前缀)，model_path 指向含 config.json 的目录 (无 safetensor_weights 子目录) |

---
