# Issue tracker: Local Markdown

Issues 与 spec 以 markdown 文件形式存放在本仓库的 `.scratch/` 下(本地开发,不用 gh / GitHub)。

## Conventions

- 一个 feature 一个目录:`.scratch/<feature-slug>/`
- spec 是 `.scratch/<feature-slug>/spec.md`
- 实现 issue 是每个 ticket 一个文件,存 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`,从 `01` 编号,**绝不**用单个合并的 tickets 文件
- triage 状态记录在每个 issue 文件顶部的一行 `Status:`(角色字符串见 `triage-labels.md`)
- 评论与对话历史追加在文件底部 `## Comments` 标题之下

## When a skill says "publish to the issue tracker"

在 `.scratch/<feature-slug>/` 下新建文件(目录不存在则先创建)。

## When a skill says "fetch the relevant ticket"

读引用路径处的文件。用户通常会直接给路径或 issue 编号。

## Wayfinding operations

供 `/wayfinder` 使用。**map** 是一个文件,每个 ticket 对应一个 **child** 文件。

- **Map**:`.scratch/<effort>/map.md`(承载 Notes / Decisions-so-far / Fog 正文)。
- **Child ticket**:`.scratch/<effort>/issues/NN-<slug>.md`,从 `01` 编号,问题写在正文。`Type:` 行记录 ticket 类型(`research`/`prototype`/`grilling`/`task`);`Status:` 行记录 `claimed`/`resolved`。
- **Blocking**:顶部附近一行 `Blocked by: NN, NN`。所有被列文件都 `resolved` 时即解除阻塞。
- **Frontier**:扫 `.scratch/<effort>/issues/`,找未关闭、未阻塞、未认领的文件;按编号取第一个。
- **Claim**:保存前先设 `Status: claimed`。
- **Resolve**:在 `## Answer` 标题下追加答案,设 `Status: resolved`,再把 context 指针(gist + 链接)追加到 `map.md` 的 Decisions-so-far。
