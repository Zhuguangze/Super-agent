# 0004 - 图片以 base64 内嵌会话历史

多模态用户消息的图片以 **base64 数据块**直接随 WS 消息上行、随 Session JSONL 落盘、回放时原样进入模型上下文——而不是先上传成服务端文件、消息里只带引用 URL。图片块与工具结果富块（ticket 24）共享同一扁平字典形状 `{type:"image", media_type, data}`，来源不同（用户上传 vs 工具产生）但存储与渲染同构。

## Considered Options

- REST 上传 + 文件引用（消息带图片 id，`<img>` 走带鉴权的 URL）——被否：需要新增图片存储目录、带 token 的静态服务端点、孤儿文件清理与生命周期管理；内网单机场景下这套基础设施的复杂度远超收益
- base64 内嵌（采纳）：零新端点；Session 的分支、Compaction、回放机制天然继承图片块，无需任何适配

## Consequences

- **单帧体积**：图片 base64 随 WS 帧上行，受 uvicorn `--ws-max-size`（默认 16MiB）约束——前端按"每条 ≤4 张、单张原始 ≤5MB、base64 总量 ≤12MB"设限，长边超 2000px 自动降采样（gif 除外，canvas 重绘会丢动画）
- **token 成本**：常驻历史意味着后续每轮都把历史图片重新发给模型；接近上限由既有 Compaction 兜底（摘要只含文字，图片随被压缩条目离开上下文）
- **Provider 路由**：openai-compat 通道必须支持 image_url parts（glm 等自定义模型的默认路由），否则功能对该路由不可用；纯文本 Provider 收到图片由 API 报错经 error 事件呈现（无模型模态元数据，不做预检）
