import { describe, expect, it } from 'vitest'
import { closeDanglingFence, renderMarkdown } from '@/utils/markdown'

describe('markdown 渲染', () => {
  it('基础 markdown 转成 HTML', () => {
    expect(renderMarkdown('**bold**')).toContain('<strong>bold</strong>')
  })

  it('代码块渲染为 pre/code', () => {
    const html = renderMarkdown('```py\nprint(1)\n```')
    expect(html).toContain('<pre')
    expect(html).toContain('<code')
  })

  it('模型输出里的 script 被消毒（独立段落，不拖累后续 markdown）', () => {
    const html = renderMarkdown('<script>alert(1)</script>\n\n**ok**')
    expect(html).toContain('<strong>ok</strong>')
    expect(html).not.toContain('<script')
  })

  it('流式期间补齐未闭合围栏', () => {
    const open = '代码：\n```py\nprint(1)'
    expect(closeDanglingFence(open)).toBe('代码：\n```py\nprint(1)\n```')
    const closed = '已闭合\n```\n内容\n```\n'
    expect(closeDanglingFence(closed)).toBe(closed)
    // 渲染层同样生效：流式下的 pre 正常闭合
    expect(renderMarkdown(open, true)).toContain('</pre>')
  })
})
