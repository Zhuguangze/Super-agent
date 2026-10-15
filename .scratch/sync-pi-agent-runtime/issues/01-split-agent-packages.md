# 01: 拆包 — agent 层按职责拆子包

**What to build:** agent 层从平铺的几个文件重构为按职责划分的子包结构,为后续逐块搬 pi 的能力腾出落点。这一步是纯结构重构——只挪函数位置、改 import 路径,逻辑一字不动,现有测试必须全绿。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] agent 层拆成 `loop/`(循环)、`queue/`(队列)、`execution/`(工具执行)三个子包,`hooks`/`types`/`tools` 保持原样
- [x] `_drain` 的两处重复实现统一到 `queue/`,app 层改为 import 而非重复定义
- [x] 现有全部测试通过,无任何行为变化

## Answer

完成。agent 层从平铺 4 文件拆成 `loop/`、`queue/`、`execution/` 三个子包(各带 `__init__.py` 导出,`from super_agent.agent.loop import agent_loop` 等旧 import 语句不变)。`_drain` 两处重复统一到 `queue/queue.py`。全量测试 269 passed / 1 skipped,与拆包前一致,行为零变化。

