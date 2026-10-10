/** markdown 渲染——marked 解析 + DOMPurify 消毒（模型输出不可信）。 */

import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

/** 流式期间补齐未闭合的 ``` 围栏，避免半截代码块吞掉后续内容。 */
export function closeDanglingFence(text: string): string {
  const count = (text.match(/```/g) ?? []).length
  return count % 2 === 1 ? `${text}\n\`\`\`` : text
}

export function renderMarkdown(text: string, streaming = false): string {
  const source = streaming ? closeDanglingFence(text) : text
  const html = marked.parse(source, { async: false })
  return DOMPurify.sanitize(html)
}
