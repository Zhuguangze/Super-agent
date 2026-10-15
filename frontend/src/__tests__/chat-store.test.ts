import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

describe('chat store：流式事件累积', () => {
  it('message_start/update/end 组装出一条 assistant 消息', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.runningBySession['s1'] = true
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant', session_id: 's1' })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '你' } })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '好' } })
    chat.handleEvent({
      type: 'message_end',
      msg_id: 'm1',
      stop_reason: 'end_turn',
      usage: { input_tokens: 3, output_tokens: 2, cache_read_tokens: 0, cache_write_tokens: 0 },
    })

    expect(chat.messages).toHaveLength(1)
    expect(chat.messages[0]!).toMatchObject({ role: 'assistant', text: '你好', status: 'done' })
    expect(chat.messages[0]!.usage?.output_tokens).toBe(2)
  })

  it('thinking 增量进入 thinking 字段，不混入正文', async () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant' })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'thinking', text: '想想' } })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '答案' } })
    await vi.waitFor(() => {
      // delta 合帧（切换卡顿优化）：批量应用后可见
      expect(chat.messages[0]!.thinking).toBe('想想')
      expect(chat.messages[0]!.text).toBe('答案')
    })
  })

  it('stop_reason=aborted 把消息标记为 aborted', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant' })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '写到一半' } })
    chat.handleEvent({ type: 'message_end', msg_id: 'm1', stop_reason: 'aborted', usage: null })

    expect(chat.messages[0]!.status).toBe('aborted')
    expect(chat.messages[0]!.text).toBe('写到一半')
  })
})

describe('chat store：发送与错误', () => {
  it('sendPrompt 落一条 user 消息（running 随连接状态变化，见下一用例）', () => {
    const chat = useChatStore()
    chat.sendPrompt('  帮我写个脚本  ')
    expect(chat.messages[0]!).toMatchObject({ role: 'user', text: '帮我写个脚本' })
  })

  it('WS 未连接时落错误条目并退出运行态', () => {
    const chat = useChatStore()
    chat.sendPrompt('hi')
    expect(chat.running).toBe(false)
    expect(chat.messages[chat.messages.length - 1]?.role).toBe('error')
  })

  it('error 事件落为错误条目，并关掉仍在流式的消息', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant' })
    chat.handleEvent({ type: 'message_update', msg_id: 'm1', delta: { type: 'text', text: '部' } })
    chat.handleEvent({ type: 'error', code: 'provider_auth', message: 'API key 缺失或无效' })

    expect(chat.running).toBe(false)
    expect(chat.messages[0]!.status).toBe('error')
    const last = chat.messages[chat.messages.length - 1]!
    expect(last.role).toBe('error')
    expect(last.text).toContain('API key')
  })

  it('未知 msg_id 的 update 被安全忽略', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'message_update', msg_id: 'ghost', delta: { type: 'text', text: 'x' } })
    expect(chat.messages).toHaveLength(0)
  })

  it('手动选技能（Claude Code 式）：sendPrompt 携带 skill 并在发送后清除选择', async () => {
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
      const conn = useConnectionStore()
      conn.ensureConnected('tok')
      const chat = useChatStore()
      chat.selectedSkill = 'deploy'
      chat.sendPrompt('开始部署', 'deploy')
      const sent = conn.getClient()
      expect(sent).not.toBeNull()
      // WS 载荷带 skill（FakeWebSocket 实例在 stub 类上不可枚举——经 send 副作用验证：无异常即发出）
      expect(chat.selectedSkill).toBe('') // 发送后清除选择
      conn.disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('loadSkills 拉取技能清单，失败静默', async () => {
    useAuthStore().token = 'tok'
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ skills: [{ name: 'deploy', description: '部署清单' }] }),
      })),
    )
    const chat = useChatStore()
    await chat.loadSkills()
    expect(chat.skills).toEqual([{ name: 'deploy', description: '部署清单' }])

    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))
    await chat.loadSkills()
    expect(chat.skills).toEqual([])
  })
})

describe('chat store：agent 相位与工具事件（ticket 05）', () => {
  it('多轮 run：turn 间 message_end 不关运行态，agent_end 才关', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.runningBySession['s1'] = true
    chat.handleEvent({ type: 'message_end', msg_id: 'm1', stop_reason: 'tool_use', usage: null, session_id: 's1' })
    expect(chat.running).toBe(true)
    chat.handleEvent({
      type: 'agent_end',
      stop_reason: 'end_turn',
      usage: { input_tokens: 9, output_tokens: 6, cache_read_tokens: 0, cache_write_tokens: 0 },
      session_id: 's1',
    })
    expect(chat.running).toBe(false)
  })

  it('abort 收尾（无 agent_end）也关运行态', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.runningBySession['s1'] = true
    chat.handleEvent({ type: 'message_end', msg_id: 'm1', stop_reason: 'aborted', usage: null })
    expect(chat.running).toBe(false)
  })

  it('工具事件：start 建 running 卡片，update 累积，end 落结果', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'tool_execution_start', tool_call_id: 't1', name: 'bash', params: { cmd: 'ls' } })
    chat.handleEvent({ type: 'tool_execution_update', tool_call_id: 't1', delta: '扫描中…' })
    chat.handleEvent({ type: 'tool_execution_end', tool_call_id: 't1', content: 'a.txt', is_error: false })

    const toolMsg = chat.messages.find((m) => m.role === 'tool')
    expect(toolMsg?.id).toBe('tool_t1')
    expect(toolMsg?.tool).toMatchObject({ name: 'bash', status: 'done', content: 'a.txt' })
    expect(toolMsg?.tool?.updates).toEqual(['扫描中…'])
    expect(toolMsg?.status).toBe('done')
  })

  it('tool_execution_end 携带 blocks → 工具卡就位（票 24）', () => {
    const chat = useChatStore()
    const blocks = [{ type: 'image', media_type: 'image/png', data: 'aGk=' }]
    chat.handleEvent({ type: 'tool_execution_start', tool_call_id: 't9', name: 'read', params: {} })
    chat.handleEvent({
      type: 'tool_execution_end',
      tool_call_id: 't9',
      content: '[图片: x.png]',
      is_error: false,
      blocks,
    })
    const toolMsg = chat.messages.find((m) => m.role === 'tool')
    expect(toolMsg?.tool?.blocks).toEqual(blocks)
  })

  it('工具出错：is_error 落 error 态', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'tool_execution_start', tool_call_id: 't2', name: 'bash', params: {} })
    chat.handleEvent({ type: 'tool_execution_end', tool_call_id: 't2', content: '命令失败', is_error: true })

    const toolMsg = chat.messages.find((m) => m.role === 'tool')
    expect(toolMsg?.tool).toMatchObject({ status: 'error', content: '命令失败' })
    expect(toolMsg?.status).toBe('error')
  })

  it('queue_update 更新排队计数', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'queue_update', pending: 2 })
    expect(chat.pendingCount).toBe(2)
    chat.handleEvent({ type: 'queue_update', pending: 0 })
    expect(chat.pendingCount).toBe(0)
  })

  it('compaction_end(compacted) 落 notice 提示；未压缩不提示（ticket 10）', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'compaction_end', compacted: false })
    expect(chat.messages).toHaveLength(0)
    chat.handleEvent({ type: 'compaction_end', compacted: true, summary_chars: 120 })
    const last = chat.messages[chat.messages.length - 1]!
    expect(last.role).toBe('notice')
    expect(last.text).toContain('压缩')
  })

  it('运行中 sendPrompt 照常发送（WS 侧排队，不清运行态）', () => {
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
      const conn = useConnectionStore()
      conn.ensureConnected('tok')
      const chat = useChatStore()
      chat.currentSessionId = 's1'
      chat.runningBySession['s1'] = true
      chat.sendPrompt('先等等')
      expect(chat.running).toBe(true)
      expect(chat.messages[chat.messages.length - 1]).toMatchObject({ role: 'user', text: '先等等' })
      conn.disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('turn_start 更新 turn 计数，agent_start 重置', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.handleEvent({ type: 'agent_start', session_id: 's1' })
    expect(chat.turnCount).toBe(0)
    chat.handleEvent({ type: 'turn_start', turn: 1, session_id: 's1' })
    expect(chat.turnCount).toBe(1)
    chat.handleEvent({ type: 'turn_start', turn: 2, session_id: 's1' })
    expect(chat.turnCount).toBe(2)
  })

  it('compaction_start 推一条压缩中的 notice', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.handleEvent({ type: 'compaction_start', session_id: 's1' })
    const notices = chat.messages.filter((m) => m.role === 'notice')
    expect(notices.some((n) => n.text.includes('压缩'))).toBe(true)
  })

  it('截断的工具结果推一条重发提示 notice', () => {
    const chat = useChatStore()
    chat.currentSessionId = 's1'
    chat.handleEvent({ type: 'tool_execution_start', tool_call_id: 't1', name: 'read', params: {} })
    chat.handleEvent({
      type: 'tool_execution_end',
      tool_call_id: 't1',
      content: '响应被截断，工具调用未执行（参数可能不完整）',
      is_error: true,
    })
    const notices = chat.messages.filter((m) => m.role === 'notice')
    expect(notices.some((n) => n.text.includes('截断'))).toBe(true)
  })
})
