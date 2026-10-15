import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageList from '@/components/MessageList.vue'
import type { ChatMessage } from '@/stores/chat'

function msg(partial: Partial<ChatMessage>): ChatMessage {
  return { id: 'm', role: 'assistant', text: '', thinking: '', status: 'done', ...partial }
}

describe('MessageList（工作台 wk 风格）', () => {
  it('assistant 消息按 markdown 渲染，user 消息纯文本气泡', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 'u1', role: 'user', text: '**原样**显示', thinking: '', status: 'done' },
          msg({ id: 'a1', text: '加粗：**ok**' }),
        ],
      },
    })
    expect(wrapper.find('.wk-asst .bubble strong').exists()).toBe(true)
    expect(wrapper.find('.wk-user').text()).toBe('**原样**显示')
  })

  it('用户图片（票 03）：缩略图网格 + lightbox 打开/关闭', async () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          {
            id: 'u1',
            role: 'user',
            text: '看这两张图',
            thinking: '',
            status: 'done',
            images: [
              { media_type: 'image/png', data: 'aGk=' },
              { media_type: 'image/jpeg', data: 'aGk=' },
            ],
          },
        ],
      },
    })
    const imgs = wrapper.findAll('.user-images img')
    expect(imgs).toHaveLength(2)
    expect(imgs[0]!.attributes('src')).toBe('data:image/png;base64,aGk=')

    await imgs[0]!.trigger('click')
    expect(wrapper.find('.img-lightbox').exists()).toBe(true)
    expect(wrapper.find('.img-lightbox img').attributes('src')).toBe('data:image/png;base64,aGk=')
    await wrapper.find('.img-lightbox').trigger('click')
    expect(wrapper.find('.img-lightbox').exists()).toBe(false)
  })

  it('纯图消息：无文字时不出文本气泡,图片照常渲染', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          {
            id: 'u2',
            role: 'user',
            text: '',
            thinking: '',
            status: 'done',
            images: [{ media_type: 'image/png', data: 'aGk=' }],
          },
        ],
      },
    })
    expect(wrapper.find('.wk-user').exists()).toBe(false)
    expect(wrapper.findAll('.user-images img')).toHaveLength(1)
  })

  it('专家聊天背景（票：专家身份背景图）:bgImage 自适应铺满(cover + 底部锚点),缺省无背景', () => {
    const withBg = mount(MessageList, {
      props: { messages: [], bgImage: '/experts/expert-rd.png' },
    })
    const style = (withBg.find('.wk-list-wrap').element as HTMLElement).style
    expect(style.backgroundImage).toContain('expert-rd.png')
    expect(style.backgroundSize).toBe('cover')
    expect(style.backgroundPosition).toBe('center bottom')
    expect(style.backgroundRepeat).toBe('no-repeat')

    const withoutBg = mount(MessageList, { props: { messages: [] } })
    const style2 = (withoutBg.find('.wk-list-wrap').element as HTMLElement).style
    expect(style2.backgroundImage).toBe('')
  })

  it('流式且无正文显示占位，aborted 显示标记', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          msg({ id: 'a1', status: 'streaming' }),
          msg({ id: 'a2', text: '半截', status: 'aborted' }),
        ],
      },
    })
    expect(wrapper.find('.pending').exists()).toBe(true)
    expect(wrapper.text()).toContain('已中止')
  })

  it('错误条目独立样式', () => {
    const wrapper = mount(MessageList, {
      props: { messages: [{ id: 'e1', role: 'error', text: 'API key 缺失', thinking: '', status: 'error' }] },
    })
    expect(wrapper.find('.wk-error').exists()).toBe(true)
  })

  it('thinking 有内容时渲染折叠块（完成态显示耗时/Token）', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          msg({ id: 'a1', thinking: '推理一下', text: '结论', thinkSeconds: 3 }),
        ],
      },
    })
    const summary = wrapper.find('.wk-think summary')
    expect(summary.text()).toContain('Thought for 3s')
    expect(wrapper.find('.wk-think-body').text()).toContain('推理一下')
  })

  it('思考中：显示 Thinking… 与实时 tokens 估算', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [msg({ id: 'a1', thinking: '这是一段比较长的思考内容用于估算 token 数', status: 'streaming' })],
      },
    })
    const summary = wrapper.find('.wk-think summary')
    expect(summary.text()).toContain('Thinking…')
    expect(wrapper.find('.think-meta').text()).toContain('tokens')
    expect(wrapper.find('.think-dot.pulse').exists()).toBe(true)
  })

  it('tool 消息渲染为工具卡片', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          msg({
            id: 'tool_t1',
            role: 'tool',
            status: 'done',
            tool: {
              toolCallId: 't1',
              name: 'bash',
              params: { cmd: 'ls' },
              status: 'done',
              content: 'a.txt',
              updates: [],
            },
          }),
        ],
      },
    })
    expect(wrapper.find('.wk-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('bash')
  })

  it('usage 含缓存命中时显示 cached 数；未命中时显示不变（票 16）', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          msg({
            id: 'a1',
            text: '答',
            usage: { input_tokens: 41500, output_tokens: 1177, cache_read_tokens: 39000, cache_write_tokens: 2500 },
          }),
          msg({
            id: 'a2',
            text: '答二',
            usage: { input_tokens: 500, output_tokens: 20, cache_read_tokens: 0, cache_write_tokens: 0 },
          }),
        ],
      },
    })
    const tags = wrapper.findAll('.usage-tag')
    expect(tags[0]!.text()).toBe('41500 · cached 39000 → 1177 tokens')
    expect(tags[1]!.text()).toBe('500 → 20 tokens')
  })

  it('分叉点消息渲染 ‹n/m› pager 并 emit switch-branch（票 17）', async () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 'u1', entryId: 'e1', role: 'user', text: '第一问', thinking: '', status: 'done' },
          {
            id: 'u2',
            entryId: 'e2',
            role: 'user',
            text: '第二问',
            thinking: '',
            status: 'done',
            branch: { index: 0, total: 2, siblings: ['e2', 'e3'] },
          },
        ],
      },
    })
    const pagers = wrapper.findAll('.branch-pager')
    expect(pagers).toHaveLength(1) // 无分叉的消息没有 pager
    expect(pagers[0]!.text()).toContain('1/2')
    // 边界：index 0 时 ‹ 禁用，› 可用
    expect(pagers[0]!.find('button.pg-prev').attributes('disabled')).toBeDefined()
    expect(pagers[0]!.find('button.pg-next').attributes('disabled')).toBeUndefined()
    await pagers[0]!.find('button.pg-next').trigger('click')
    expect(wrapper.emitted('switch-branch')).toEqual([['e3']])
  })

  it('notice 消息渲染为居中提示条（压缩标记）', () => {
    const wrapper = mount(MessageList, {
      props: { messages: [msg({ id: 'n1', role: 'notice', text: '会话已压缩', status: 'done' })] },
    })
    expect(wrapper.find('.wk-note').exists()).toBe(true)
    expect(wrapper.text()).toContain('会话已压缩')
  })

  it('挂载即满列表时强制滚到底（刷新后历史回放场景）', async () => {
    const scrollTo = vi.fn()
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>
    const original = proto.scrollTo
    proto.scrollTo = scrollTo
    try {
      const wrapper = mount(MessageList, {
        props: {
          messages: [msg({ id: 'a1', text: '历史一' }), msg({ id: 'a2', text: '历史二' })],
        },
      })
      const el = wrapper.find('.wk-scroll').element as HTMLElement
      Object.defineProperty(el, 'scrollHeight', { value: 900, configurable: true })
      await wrapper.vm.$nextTick()
      await wrapper.vm.$nextTick()
      expect(scrollTo).toHaveBeenCalled()
    } finally {
      proto.scrollTo = original
    }
  })

  it('离开底部显示回底按钮，滚回消失，点击平滑滚动（贴底跟随）', async () => {
    const wrapper = mount(MessageList, {
      props: { messages: [msg({ id: 'a1', text: '答案' })] },
    })
    expect(wrapper.find('.jump-bottom').exists()).toBe(false) // 初始贴底

    const el = wrapper.find('.wk-scroll').element as HTMLElement
    const setMetrics = (top: number) => {
      Object.defineProperty(el, 'scrollTop', { value: top, configurable: true, writable: true })
      Object.defineProperty(el, 'scrollHeight', { value: 800, configurable: true, writable: true })
      Object.defineProperty(el, 'clientHeight', { value: 200, configurable: true, writable: true })
    }

    // 上翻离底 600px → 按钮出现
    setMetrics(0)
    await wrapper.find('.wk-scroll').trigger('scroll')
    expect(wrapper.find('.jump-bottom').exists()).toBe(true)

    // 点击 → 平滑滚到 scrollHeight
    const scrollTo = vi.fn()
    ;(el as unknown as Record<string, unknown>).scrollTo = scrollTo
    await wrapper.find('.jump-bottom').trigger('click')
    expect(scrollTo).toHaveBeenCalledWith({ top: 800, behavior: 'smooth' })

    // 滚回底部 → 按钮消失
    setMetrics(600)
    await wrapper.find('.wk-scroll').trigger('scroll')
    expect(wrapper.find('.jump-bottom').exists()).toBe(false)
  })

  it('贴底时新消息自动跟随滚动；上翻时不打扰', async () => {
    const scrollTo = vi.fn()
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>
    const original = proto.scrollTo
    proto.scrollTo = scrollTo
    try {
      const wrapper = mount(MessageList, {
        props: { messages: [msg({ id: 'a1', text: '一' })] },
      })
      const el = wrapper.find('.wk-scroll').element as HTMLElement
      Object.defineProperty(el, 'scrollTop', { value: 600, configurable: true, writable: true })
      Object.defineProperty(el, 'scrollHeight', { value: 800, configurable: true, writable: true })
      Object.defineProperty(el, 'clientHeight', { value: 200, configurable: true, writable: true })
      await wrapper.vm.$nextTick()

      await wrapper.setProps({ messages: [msg({ id: 'a1', text: '一' }), msg({ id: 'a2', text: '二' })] })
      await wrapper.vm.$nextTick()
      expect(scrollTo).toHaveBeenCalled() // 贴底 → 跟随

      // 上翻离底 → 新消息不再自动滚
      scrollTo.mockClear()
      Object.defineProperty(el, 'scrollTop', { value: 0, configurable: true, writable: true })
      await wrapper.find('.wk-scroll').trigger('scroll')
      await wrapper.setProps({
        messages: [msg({ id: 'a1', text: '一' }), msg({ id: 'a2', text: '二' }), msg({ id: 'a3', text: '三' })],
      })
      await wrapper.vm.$nextTick()
      expect(scrollTo).not.toHaveBeenCalled()
    } finally {
      proto.scrollTo = original
    }
  })
})
