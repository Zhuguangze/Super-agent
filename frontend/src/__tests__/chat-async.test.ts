import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import { useAuthStore } from '@/stores/auth'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3

  url: string
  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  sent: Record<string, unknown>[] = []

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data))
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
  }

  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  localStorage.clear()
  setActivePinia(createPinia())
  useAuthStore().token = 'test-token'
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function connectAndOpen(): FakeWebSocket {
  useConnectionStore().ensureConnected('test-token')
  const sock = FakeWebSocket.instances[0]!
  sock.serverOpen()
  return sock
}

const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0 }

// 多会话并发运行(ADR-0005):状态仓按会话分桶
describe('多会话并发:事件路由与运行态', () => {
  it('事件按 session_id 路由:后台会话的流式消息不进当前视图,切回可见', async () => {
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-b'

    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant', session_id: 'sess-a' })
    chat.handleEvent({
      type: 'message_update',
      msg_id: 'm1',
      delta: { type: 'text', text: 'A 的回复' },
      session_id: 'sess-a',
    })
    expect(chat.messages).toEqual([]) // 当前视图(B)不受后台会话影响

    chat.currentSessionId = 'sess-a'
    await vi.waitFor(() => expect(chat.messages[0]?.text).toBe('A 的回复')) // 合帧冲刷后可见
    expect(chat.messages[0]).toMatchObject({ role: 'assistant', status: 'streaming' })
  })

  it('agent_end 只清对应会话的运行态', () => {
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-a'
    chat.sendPrompt('A 任务')
    expect(chat.running).toBe(true)

    chat.currentSessionId = 'sess-b'
    expect(chat.running).toBe(false) // B 没在跑

    chat.handleEvent({ type: 'agent_end', stop_reason: 'end_turn', usage: USAGE, session_id: 'sess-a' })
    chat.currentSessionId = 'sess-a'
    expect(chat.running).toBe(false) // A 的收尾只清 A
  })

  it('运行中切换会话不被拦截,switch_session 帧照发', () => {
    const chat = useChatStore()
    const sock = connectAndOpen()
    chat.currentSessionId = 'sess-a'
    chat.sendPrompt('A 任务')

    chat.switchSession('sess-b')
    expect(sock.sent.find((m) => m.cmd === 'switch_session')).toMatchObject({ session_id: 'sess-b' })
  })

  it('切回运行中的会话:活缓冲优先,不触发回放覆盖', () => {
    const fetchCalls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        fetchCalls.push(String(input))
        return new Response(JSON.stringify({ entries: [] }), { status: 200 })
      }),
    )
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-a'
    chat.sendPrompt('A 任务')

    chat.currentSessionId = 'sess-b'
    chat.handleEvent({ type: 'session_info', session_id: 'sess-a', model: 'm' }) // 切回 A

    expect(chat.currentSessionId).toBe('sess-a')
    expect(chat.messages[0]).toMatchObject({ role: 'user', text: 'A 任务' }) // 活缓冲保留
    expect(fetchCalls.some((u) => u.includes('/api/sessions/sess-a'))).toBe(false) // 未触发回放
  })

  it('后台会话的审批:保存并弹最早的,携带来源会话', () => {
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-b'

    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr1',
      tool: 'bash',
      params: {},
      session_id: 'sess-a',
    })
    expect(chat.pendingApproval).toMatchObject({ approvalId: 'apr1', sessionId: 'sess-a' })

    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr2',
      tool: 'bash',
      params: {},
      session_id: 'sess-b',
    })
    expect(chat.pendingApproval?.approvalId).toBe('apr1') // 最早优先
  })

  it('后台会话完成 → 未读标记;进入会话清除', async () => {
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-b'

    chat.handleEvent({
      type: 'agent_end',
      stop_reason: 'end_turn',
      usage: USAGE,
      session_id: 'sess-a',
    })
    expect(chat.unreadBySession['sess-a']).toBe(true)

    chat.currentSessionId = 'sess-a'
    await nextTick() // 清除走 watch(_flush: pre)
    expect(chat.unreadBySession['sess-a']).toBe(false)
  })

  // ---- 切换卡顿优化(grilling:Q7-A delta 合帧 / Q7-B 会话桶缓存) ----

  it('流式 delta 合帧:100ms 内的多次 update 批量应用一次', () => {
    vi.useFakeTimers()
    try {
      const chat = useChatStore()
      connectAndOpen()
      chat.currentSessionId = 'sess-a'
      chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant', session_id: 'sess-a' })

      chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '你' }, session_id: 'sess-a' })
      expect(chat.messages[0]?.text).toBe('') // 尚未应用(合帧中)

      vi.advanceTimersByTime(120)
      expect(chat.messages[0]?.text).toBe('你')

      // 高频连发 3 条 → 只在一次冲刷后全部到位
      chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '好' }, session_id: 'sess-a' })
      chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '吗' }, session_id: 'sess-a' })
      chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '?' }, session_id: 'sess-a' })
      expect(chat.messages[0]?.text).toBe('你')
      vi.advanceTimersByTime(120)
      expect(chat.messages[0]?.text).toBe('你好吗?')
    } finally {
      vi.useRealTimers()
    }
  })

  it('message_end 立即冲刷该消息的待应用增量,不等定时器', () => {
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-a'
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant', session_id: 'sess-a' })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '你' }, session_id: 'sess-a' })

    chat.handleEvent({ type: 'message_end', msg_id: 'm1', stop_reason: 'end_turn', usage: USAGE, session_id: 'sess-a' })
    expect(chat.messages[0]).toMatchObject({ text: '你', status: 'done' })
  })

  it('切回已看过的会话:命中桶缓存,不重新回放', () => {
    const fetchCalls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        fetchCalls.push(String(input))
        return new Response(JSON.stringify({ entries: [] }), { status: 200 })
      }),
    )
    const chat = useChatStore()
    connectAndOpen()
    chat.currentSessionId = 'sess-a'
    chat.sendPrompt('A 任务')
    chat.handleEvent({ type: 'agent_end', stop_reason: 'end_turn', usage: USAGE, session_id: 'sess-a' }) // A 已完成

    chat.handleEvent({ type: 'session_info', session_id: 'sess-b', model: 'm' }) // 切到 B
    chat.handleEvent({ type: 'session_info', session_id: 'sess-a', model: 'm' }) // 切回 A

    expect(chat.messages[0]).toMatchObject({ role: 'user', text: 'A 任务' }) // 缓冲保留
    expect(fetchCalls.some((u) => u.includes('/api/sessions/sess-a'))).toBe(false) // 未重拉
  })
})
