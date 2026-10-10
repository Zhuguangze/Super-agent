import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SessionList from '@/components/SessionList.vue'
import MessageList from '@/components/MessageList.vue'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { useConnectionStore } from '@/stores/connection'
import type { SessionSummary } from '@/api/rest'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

function session(partial: Partial<SessionSummary>): SessionSummary {
  return {
    id: 's1',
    updated_at: 0,
    title: '查 readiness NaN',
    msg_count: 9,
    total_tokens: 14200,
    pinned: false,
    running: false,
    ...partial,
  }
}

describe('SessionList 侧栏（工作台 wk-side）', () => {
  it('渲染会话列表（标题/消息数），当前会话高亮', () => {
    const chat = useChatStore()
    chat.sessions = [
      session({ id: 's1' }),
      session({ id: 's2', title: 'M4 compaction 设计', msg_count: 23 }),
    ]
    chat.currentSessionId = 's2'
    const wrapper = mount(SessionList, { global: { stubs: { RouterLink: true } } })
    const items = wrapper.findAll('.wk-sess')
    expect(items).toHaveLength(2)
    expect(items[1]!.classes()).toContain('active')
    expect(items[1]!.text()).toContain('M4 compaction 设计')
    expect(items[1]!.text()).toContain('23 条')
  })

  it('running 会话带脉冲点', () => {
    const chat = useChatStore()
    chat.sessions = [session({ id: 's1', running: true })]
    const wrapper = mount(SessionList, { global: { stubs: { RouterLink: true } } })
    expect(wrapper.find('.run-dot').exists()).toBe(true)
  })

  it('点击条目发出切换', async () => {
    class FakeWebSocket {
      static OPEN = 1
      readyState = 1
      sent: string[] = []
      send(data: string): void {
        this.sent.push(data)
      }
      close(): void {}
      onopen: unknown = null
      onmessage: unknown = null
      onclose: unknown = null
      onerror: unknown = null
    }
    vi.stubGlobal('WebSocket', FakeWebSocket)
    try {
      useConnectionStore().ensureConnected('tok')
      const sendSpy = vi.spyOn(useConnectionStore().getClient()!, 'send')
      const chat = useChatStore()
      chat.sessions = [session({ id: 's1' })]

      const wrapper = mount(SessionList, { global: { stubs: { RouterLink: true } } })
      await wrapper.find('.wk-sess').trigger('click')
      expect(sendSpy).toHaveBeenCalledWith('switch_session', { session_id: 's1' })
      useConnectionStore().disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('模型与用量（ticket 14）', () => {
  beforeEach(() => {
    // 自动回 {type:'response', ok:true} 的假 WS——send 的 promise 才能完成
    class FakeWebSocket {
      static OPEN = 1
      readyState = 1
      sent: string[] = []
      onmessage: ((ev: { data: string }) => void) | null = null
      onopen: unknown = null
      onclose: unknown = null
      onerror: unknown = null
      send(data: string): void {
        this.sent.push(data)
        const msg = JSON.parse(data) as { req_id?: string }
        if (msg.req_id) {
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({ type: 'response', req_id: msg.req_id, ok: true }),
            })
          }, 0)
        }
      }
      close(): void {}
    }
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  it('setModel 发送命令，成功后更新本地状态', async () => {
    useConnectionStore().ensureConnected('tok')
    const chat = useChatStore()
    chat.setModel('claude-haiku-4-5')
    await vi.waitFor(() => expect(chat.currentModel).toBe('claude-haiku-4-5'))
    useConnectionStore().disconnect()
  })

  it('setThinkingLevel 更新本地 effort', async () => {
    useConnectionStore().ensureConnected('tok')
    const chat = useChatStore()
    chat.setThinkingLevel('max')
    await vi.waitFor(() => expect(chat.currentEffort).toBe('max'))
    useConnectionStore().disconnect()
  })

  it('loadUsage 拉取用量，失败静默', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          usage: { input_tokens: 10, output_tokens: 5, cache_read_tokens: 0, cache_write_tokens: 0 },
        }),
      })),
    )
    useAuthStore().token = 'tok'
    const chat = useChatStore()
    await chat.loadUsage()
    expect(chat.usage?.input_tokens).toBe(10)
  })
})

describe('MessageList 分叉入口（ticket 09）', () => {
  it('历史消息 hover 出分叉按钮并 emit branch', async () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 'e1', entryId: 'e1', role: 'user', text: '第一问', thinking: '', status: 'done' },
        ],
      },
    })
    const btn = wrapper.find('.fork-btn')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(wrapper.emitted('branch')).toEqual([['e1']])
  })

  it('流式/错误消息没有 entryId，不出分叉按钮', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 'm1', role: 'assistant', text: '流式', thinking: '', status: 'streaming' },
          { id: 'err', role: 'error', text: '错误', thinking: '', status: 'error' },
        ],
      },
    })
    expect(wrapper.find('.fork-btn').exists()).toBe(false)
  })
})
