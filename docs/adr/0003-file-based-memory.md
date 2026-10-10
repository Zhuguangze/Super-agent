# 0003 - 长期记忆采用文件式而非向量库

长期记忆的候选：向量 RAG（Milvus 自动抽取+检索）、结构化库（Mongo/Neo4j）、文件式。选择**文件式**：`data/memory/<user_id>/` 下 `MEMORY.md` 索引 + 单事实文件（frontmatter + 正文），agent 通过内置 memory 工具自主读写，索引常驻系统提示、细节按需加载。理由：透明可审计（用户可直接查看修改自己的记忆）、零新依赖、目录即多用户隔离边界、记忆跟着对话自然生长而不依赖容易产生噪音的抽取管线。

## Considered Options

- 向量 RAG（Milvus）——被否（v1）：抽取管线噪音风险高、运维成本前置；但文件是 source of truth，v2 可在文件层上加向量索引随时重建，无需迁移数据
- 结构化库——被否：建模成本高，agent 难以自主维护

## Consequences

- 检索精度依赖 agent 自主性（索引 + 按需 view）而非相似度匹配；若实际使用中召回不足，v2 补索引层
- 这是继 Tool Approval（ADR-0001）之后第二个对 pi 的有意增强——pi 本身没有长期记忆子系统
