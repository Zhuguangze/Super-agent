# 0002 - 会话格式照搬 pi 的 JSONL append-only 树

多用户 Agent 的会话持久化面临 SQLite 与文件格式的选择。我们照搬 pi 的 **append-only JSONL 树**：文件首行 header，其后每行一个条目（`id`/`parentId` 构成树），活跃分支即根到叶路径，重放即重建上下文；存 `data/sessions/<user_id>/`。理由：该格式久经验证、人可直接读、pi 的分支/压缩/重放逻辑可近乎直接移植；本产品的读取模式是"按用户取整段会话"，SQLite 的查询优势用不上。

## Considered Options

- SQLite——被否：树结构与重放逻辑需要重新设计，且一旦有数据迁移成本高；检索类需求（v2 语义搜索）应通过在 JSONL 之上加索引层解决，不动存储格式

## Consequences

- 格式带 `version` 字段，为未来迁移留位（pi 已经历 v1→v2→v3 三版迁移）
- 压缩（compaction）条目必须携带 `first_kept_entry_id`，重放语义依赖它
