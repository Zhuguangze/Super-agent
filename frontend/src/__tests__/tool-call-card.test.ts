import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCallCard from '@/components/ToolCallCard.vue'
import type { ToolCallView } from '@/stores/chat'

function tool(partial: Partial<ToolCallView>): ToolCallView {
  return {
    toolCallId: 't1',
    name: 'bash',
    params: { cmd: 'ls' },
    status: 'running',
    content: '',
    updates: [],
    ...partial,
  }
}

describe('ToolCallCard 三态（一行式卡片）', () => {
  it('running：显示执行中与最新进度增量', () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: tool({ updates: ['扫描中…', '读取目录…'] }) },
    })
    expect(wrapper.text()).toContain('bash')
    expect(wrapper.text()).toContain('读取目录…') // 只取最新一条
    expect(wrapper.text()).not.toContain('扫描中…')
  })

  it('done：点击展开可见结果', async () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: tool({ status: 'done', content: 'a.txt\nb.txt' }) },
    })
    expect(wrapper.text()).not.toContain('a.txt') // 默认收起
    await wrapper.find('.wk-card').trigger('click')
    expect(wrapper.text()).toContain('a.txt')
  })

  it('error：出错标记，展开可见错误内容', async () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: tool({ status: 'error', content: '命令失败：exit 1' }) },
    })
    expect(wrapper.find('.wk-card--error').exists()).toBe(true)
    expect(wrapper.text()).toContain('出错')
    await wrapper.find('.wk-card').trigger('click')
    expect(wrapper.text()).toContain('命令失败')
  })

  it('参数折叠展示（JSON，点击展开）', async () => {
    const wrapper = mount(ToolCallCard, { props: { tool: tool({}) } })
    expect(wrapper.text()).toContain('{"cmd":"ls"}') // args 一行省略是紧凑 JSON
    await wrapper.find('.wk-card').trigger('click')
    expect(wrapper.find('.detail-pre').text()).toContain('"cmd": "ls"')
  })
})

describe('ToolCallCard 富块渲染（票 24）', () => {
  const imageBlocks = [
    { type: 'image', media_type: 'image/png', data: 'aGk=' },
  ]

  it('image block 常显渲染为缩略图（data URI）', () => {
    const wrapper = mount(ToolCallCard, { props: { tool: tool({ status: 'done', blocks: imageBlocks }) } })
    const img = wrapper.find('.card-blocks img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('data:image/png;base64,aGk=')
  })

  it('text block 渲染为文本', () => {
    const wrapper = mount(ToolCallCard, {
      props: { tool: tool({ status: 'done', blocks: [{ type: 'text', text: '结果说明' }] }) },
    })
    expect(wrapper.find('.card-blocks').text()).toContain('结果说明')
  })

  it('点击缩略图打开页内 lightbox，再点关闭', async () => {
    const wrapper = mount(ToolCallCard, { props: { tool: tool({ status: 'done', blocks: imageBlocks }) } })
    expect(wrapper.find('.lightbox').exists()).toBe(false)
    await wrapper.find('.card-blocks img').trigger('click')
    expect(wrapper.find('.lightbox').exists()).toBe(true)
    await wrapper.find('.lightbox').trigger('click')
    expect(wrapper.find('.lightbox').exists()).toBe(false)
  })

  it('无 blocks 的卡片不渲染富块区（存量视觉不变）', () => {
    const wrapper = mount(ToolCallCard, { props: { tool: tool({ status: 'done', content: '纯文本' }) } })
    expect(wrapper.find('.card-blocks').exists()).toBe(false)
  })
})
