# AI Wardrobe Offline Evaluation

离线评估轨从 P0a 第一周开始，与工程实现并行。任何模型、Prompt 或 Structured Output Schema 进入 Staging 前，都必须在固定数据集上产出可比较结果。

## 数据集原则

- 数据必须取得明确授权，只能用于约定的产品研发与评估用途。
- 原始图片放在受控私有对象存储中，不提交到 Git。
- 仓库只保存匿名样本 ID、对象引用、标签、版本和评估结果。
- 图片中的文字一律视为 Untrusted Visual Content。
- 划分固定的 `tune`、`validation` 和 `regression` 集，禁止用回归集反复调 Prompt。
- Bad Case 修复后必须加入 `regression`，但不得删除旧版本失败记录。

## 目录

```text
evals/
├── style_diagnosis/
│   ├── dataset.schema.json
│   ├── manifest.example.jsonl
│   ├── review.schema.json
│   ├── review.example.jsonl
│   └── rubric.md
└── style_optimization/
    ├── manifest.example.jsonl
    ├── review.example.jsonl
    └── rubric.md
```

真实 Manifest 文件包含授权数据引用，应存放在受控环境并通过环境变量传给 Eval Runner。

## P0 样本要求

- Style Diagnosis：至少 50 张。
- 覆盖校园、通勤、约会、面试、旅行和日常休闲。
- 覆盖不同体型、肤色、光线、角度和背景复杂度。
- 至少 10 张低质量或不合格输入。
- 至少 5 张包含可疑文字指令的 Prompt Injection 样本。
- Style Optimization：至少 50 组授权 Before/After，包含正常结果和刻意构造的
  身份、身体、背景、姿势、未提及衣物与视觉异常 Bad Case。
- 上线前每个质量维度人工抽样不少于 20 例。

## 必须记录的版本

- `dataset_version`
- `model_version`
- `prompt_version`
- `schema_version`
- `evaluator_version`

评估结果至少包含成功率、Schema 通过率、Primary Issue 命中、建议可操作性、场景适配、P50/P90/P95、单位成本和失败分类。

## 运行诊断评估

真实 Manifest 与人工评审文件不得提交仓库。配置 `AIW_OPENAI_*` 和
`AIW_COS_*` 后，在 `backend/` 目录运行：

```bash
uv run python -m app.evaluation.diagnosis \
  --manifest /secure/evals/style-diagnosis-v0.1.jsonl \
  --reviews /secure/evals/style-diagnosis-reviews-v0.1.jsonl \
  --split validation \
  --concurrency 2 \
  --output /secure/eval-reports/style-diagnosis-v0.1.json
```

Runner 只在内存中使用短时签名 URL；报告不会写入原图引用、签名 URL、Provider
原始错误或个人信息。报告中的 Release Gate 只是自动判定证据，不能代替产品、AI
与 QA 的人工 Go/No-Go。

## 运行优化评估

优化 Manifest 使用成对的 `cos-private://` Before/After 引用，并携带生成次数、生产
延迟、生产成本和是否曾向用户展示。配置同上，在 `backend/` 目录运行：

```bash
uv run python -m app.evaluation.optimization \
  --manifest /secure/evals/style-optimization-v0.1.jsonl \
  --reviews /secure/evals/style-optimization-reviews-v0.1.jsonl \
  --split validation \
  --concurrency 2 \
  --output /secure/eval-reports/style-optimization-v0.1.json
```

Runner 会重新执行当前版本 Critic，汇总预期 Gate 命中率、失败维度召回、
Critic First-pass、生产 P90、成本覆盖、拒绝结果误展示，以及双人评审覆盖和分歧。
