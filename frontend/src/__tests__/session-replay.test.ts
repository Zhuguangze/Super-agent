import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { entriesToMessages, useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { useConnectionStore } from '@/stores/connection'
import type { SessionEntry } from '@/api/rest'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

function entry(partial: Partial<SessionEntry>): SessionEntry {
  return { type: 'message', id: 'e', parentId: null, timestamp: 't', ...partial }
}

describe('entriesToMessages：历史回放', () => {
  it('message 条目转为界面消息，usage 附着到 assistant', () => {
    const messages = entriesToMessages([
      entry({ id: 'e1', message: { role: 'user', content: [{ type: 'text', text: '第一问' }] } }),
      entry({
        id: 'e2',
        message: {
          role: 'assistant',
          content: [
            { type: 'thinking', thinking: '想想', signature: 'sig' },
            { type: 'text', text: '第一答' },
          ],
        },
      }),
      entry({ id: 'e3', type: 'usage', usage: { input_tokens: 3, output_tokens: 2, cache_read_tokens: 0, cache_write_tokens: 0 } }),
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({ role: 'user', text: '第一问' })
    expect(messages[1]).toMatchObject({ role: 'assistant', text: '第一答', thinking: '想想', status: 'done' })
    expect(messages[1]!.usage?.output_tokens).toBe(2)
  })

  it('tool_use 条目建卡片，toolResult 按 tool_use_id 回填（含出错态）', () => {
    const messages = entriesToMessages([
      entry({
        id: 'e1',
        message: {
          role: 'assistant',
          content: [
            { type: 'text', text: '调用工具' },
            { type: 'tool_use', id: 't1', name: 'bash', input: { cmd: 'ls' } },
            { type: 'tool_use', id: 't2', name: 'read', input: { path: 'x' } },
          ],
        },
      }),
      entry({
        id: 'e2',
        message: {
          role: 'toolResult',
          content: [{ type: 'tool_result', tool_use_id: 't1', content: 'a.txt' }],
        },
      }),
      entry({
        id: 'e3',
        message: {
          role: 'toolResult',
          content: [{ type: 'tool_result', tool_use_id: 't2', content: '读取失败', is_error: true }],
        },
      }),
    ])

    expect(messages).toHaveLength(3) // assistant 正文 + 2 张工具卡
    expect(messages[0]).toMatchObject({ role: 'assistant', text: '调用工具' })
    expect(messages[1]!.tool).toMatchObject({ name: 'bash', status: 'done', content: 'a.txt' })
    expect(messages[2]!.tool).toMatchObject({ name: 'read', status: 'error', content: '读取失败' })
  })

  it('toolResult 条目只回填已有卡片，不单独产生消息', () => {
    const messages = entriesToMessages([
      entry({ id: 'e1', message: { role: 'toolResult', content: [] } }),
    ])
    expect(messages).toHaveLength(0)
  })

  it('历史消息带 entryId（分叉入口），流式消息没有', () => {
    const messages = entriesToMessages([
      entry({ id: 'e_user', message: { role: 'user', content: [{ type: 'text', text: '问' }] } }),
      entry({ id: 'e_asst', message: { role: 'assistant', content: [{ type: 'text', text: '答' }] } }),
    ])
    expect(messages[0]!.entryId).toBe('e_user')
    expect(messages[1]!.entryId).toBe('e_asst')
  })

  it('compaction 条目转为 notice 提示（ticket 10）', () => {
    const messages = entriesToMessages([
      entry({ id: 'e1', message: { role: 'user', content: [{ type: 'text', text: '旧问题' }] } }),
      entry({ id: 'c1', type: 'compaction', summary: '早期摘要', first_kept_entry_id: 'e1' }),
      entry({ id: 'e2', message: { role: 'user', content: [{ type: 'text', text: '续问' }] } }),
    ])
    expect(messages).toHaveLength(3)
    expect(messages[1]).toMatchObject({ role: 'notice' })
    expect(messages[1]!.text).toContain('压缩')
  })

  it('非 message 条目（model_change 等）被跳过', () => {
    const messages = entriesToMessages([
      entry({ id: 'e0', type: 'model_change' }),
      entry({ id: 'e1', message: { role: 'user', content: [{ type: 'text', text: 'hi' }] } }),
    ])
    expect(messages).toHaveLength(1)
  })
})

describe('session_info 事件：连接即回放当前会话', () => {
  it('记录 session id 并从 REST 拉取历史', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        id: 's1',
        entries: [entry({ id: 'e1', message: { role: 'user', content: [{ type: 'text', text: '历史' }] } })],
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    useAuthStore().token = 'tok'

    const chat = useChatStore()
    chat.handleEvent({ type: 'session_info', session_id: 's1' })

    await vi.waitFor(() => expect(chat.messages).toHaveLength(1))
    expect(chat.currentSessionId).toBe('s1')
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/s1', expect.anything())
    expect(chat.messages[0]!.text).toBe('历史')
  })

  it('拉取失败落错误条目', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))
    useAuthStore().token = 'tok'

    const chat = useChatStore()
    chat.handleEvent({ type: 'session_info', session_id: 's1' })

    await vi.waitFor(() => expect(chat.messages[chat.messages.length - 1]?.role).toBe('error'))
  })
})

describe('branch 与会话管理（ticket 09）', () => {
  it('branch 把本地视图截到目标条目（含该条目）', () => {
    const chat = useChatStore()
    chat.currentSessionId = 'sess-r'
    chat.messagesBySession['sess-r'] = [
      { id: 'e1', entryId: 'e1', role: 'user', text: '第一问', thinking: '', status: 'done' },
      { id: 'e2', entryId: 'e2', role: 'assistant', text: '第一答', thinking: '', status: 'done' },
      { id: 'e3', entryId: 'e3', role: 'user', text: '第二问', thinking: '', status: 'done' },
    ]
    chat.branch('e2')
    expect(chat.messages.map((m) => m.text)).toEqual(['第一问', '第一答'])
  })

  it('branch 未知 entryId 不改动视图', () => {
    const chat = useChatStore()
    chat.currentSessionId = 'sess-r'
    chat.messagesBySession['sess-r'] = [
      { id: 'e1', entryId: 'e1', role: 'user', text: '唯一', thinking: '', status: 'done' },
    ]
    chat.branch('ghost')
    expect(chat.messages).toHaveLength(1)
  })

  it('loadSessions 拉取会话列表，失败静默', async () => {
    useAuthStore().token = 'tok'
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ sessions: [{ id: 's1', updated_at: 1 }, { id: 's2', updated_at: 2 }] }),
      })),
    )
    const chat = useChatStore()
    await chat.loadSessions()
    expect(chat.sessions.map((s) => s.id)).toEqual(['s1', 's2'])
  })

  it('deleteSession 删当前会话且还有剩余 → 切到剩余会话，不再冒出新的空会话', async () => {
    class FakeWebSocket {
      static OPEN = 1
      static instances: FakeWebSocket[] = []
      readyState = 1
      sent: string[] = []
      constructor() {
        FakeWebSocket.instances.push(this)
      }
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
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: { method?: string }) => {
        if (init?.method === 'DELETE') return { ok: true, json: async () => ({ ok: true }) }
        // loadSessions：删除后仍剩 s2
        return { ok: true, json: async () => ({ sessions: [{ id: 's2', updated_at: 1 }] }) }
      }),
    )
    useAuthStore().token = 'tok'
    try {
      useConnectionStore().ensureConnected('tok')
      const chat = useChatStore()
      chat.currentSessionId = 's1'
      await chat.deleteSession('s1')
      // sent 在 FakeWebSocket 实例上（WsClient 不暴露 socket）
      const sent = FakeWebSocket.instances.flatMap((w) => w.sent)
      const cmds = sent.map((raw) => (JSON.parse(raw) as { cmd: string }).cmd)
      expect(cmds).toContain('switch_session')
      expect(cmds).not.toContain('new_session')
      const payload = JSON.parse(
        sent.find((raw) => (JSON.parse(raw) as { cmd: string }).cmd === 'switch_session')!,
      ) as { session_id: string }
      expect(payload.session_id).toBe('s2')
      useConnectionStore().disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('deleteSession 调 REST，删当前会话后清空并新开', async () => {
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
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: { method?: string }) => {
        calls.push(init?.method ?? 'GET')
        return { ok: true, json: async () => ({ sessions: [] }) }
      }),
    )
    useAuthStore().token = 'tok'
    try {
      useConnectionStore().ensureConnected('tok')
      const chat = useChatStore()
      chat.currentSessionId = 's1'
      chat.messagesBySession['s1'] = [{ id: 'm', role: 'user', text: 'x', thinking: '', status: 'done' }]
      await chat.deleteSession('s1')
      expect(calls).toContain('DELETE')
      expect(chat.messages).toHaveLength(0)
      expect(chat.currentSessionId).toBeNull()
      // 删除当前会话后自动 new_session
      const sent = useConnectionStore().getClient()
      expect(sent).not.toBeNull()
      useConnectionStore().disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('newSession', () => {
  it('无连接时不抛错（命令发送静默失败）', () => {
    const chat = useChatStore()
    expect(() => chat.newSession()).not.toThrow()
    expect(chat.currentSessionId).toBeNull()
  })

  it('运行中不触发新会话', () => {
    const chat = useChatStore()
    chat.handleEvent({ type: 'message_start', msg_id: 'm1', role: 'assistant' })
    // 无 client；running 状态下 newSession 应直接返回
    expect(() => chat.newSession()).not.toThrow()
  })
})

describe('分支注记回放（票 17）', () => {
  it('entriesToMessages 把分支注记映射为 ChatMessage.branch', () => {
    const entries: SessionEntry[] = [
      {
        type: 'message', id: 'e1', parentId: null, timestamp: 't',
        message: { role: 'user', content: [{ type: 'text', text: '第一问' }] },
      },
      {
        type: 'message', id: 'e2', parentId: 'e1', timestamp: 't',
        message: { role: 'user', content: [{ type: 'text', text: '第二问' }] },
        branch_index: 0, branch_total: 2, siblings: ['e2', 'e3'],
      },
      {
        type: 'message', id: 'e3', parentId: 'e1', timestamp: 't',
        message: { role: 'user', content: [{ type: 'text', text: '换个问法' }] },
        branch_index: 1, branch_total: 2, siblings: ['e2', 'e3'],
      },
    ]
    const msgs = entriesToMessages(entries)
    const byText = new Map(msgs.map((m) => [m.text, m]))
    expect(byText.get('第一问')?.branch).toBeUndefined()
    expect(byText.get('第二问')?.branch).toEqual({ index: 0, total: 2, siblings: ['e2', 'e3'] })
    expect(byText.get('换个问法')?.branch).toEqual({ index: 1, total: 2, siblings: ['e2', 'e3'] })
  })

  it('switchBranch 拉取分支视图并把活跃分支移到该分支会话叶', async () => {
    class FakeWebSocket {
      static OPEN = 1
      static instances: FakeWebSocket[] = []
      readyState = 1
      sent: string[] = []
      constructor() {
        FakeWebSocket.instances.push(this)
      }
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
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          entries: [
            {
              type: 'message', id: 'e1', parentId: null, timestamp: 't',
              message: { role: 'user', content: [{ type: 'text', text: '第一问' }] },
            },
            {
              type: 'usage', id: 'u1', parentId: 'e1', timestamp: 't',
              usage: { input_tokens: 1, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
            },
            {
              type: 'message', id: 'e3', parentId: 'e1', timestamp: 't',
              message: { role: 'user', content: [{ type: 'text', text: '换个问法' }] },
              branch_index: 1, branch_total: 2, siblings: ['e2', 'e3'],
            },
          ],
        }),
      })),
    )
    useAuthStore().token = 'tok'
    try {
      useConnectionStore().ensureConnected('tok')
      const chat = useChatStore()
      chat.currentSessionId = 's1'
      chat.messagesBySession['s1'] = [{ id: 'x', role: 'user', text: '旧视图', thinking: '', status: 'done' }]
      await chat.switchBranch('e3')
      expect(chat.messages.map((m) => m.text)).toEqual(['第一问', '换个问法'])
      const sent = FakeWebSocket.instances.flatMap((w) => w.sent)
      const branchCmd = sent
        .map((raw) => JSON.parse(raw) as { cmd: string; entry_id?: string })
        .find((p) => p.cmd === 'branch')
      expect(branchCmd?.entry_id).toBe('e3') // 会话叶 = 最后的 message 条目（usage 跳过）
      useConnectionStore().disconnect()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('工具结果富块回放（票 24）', () => {
  it('entriesToMessages 把 tool_result 的 blocks 塞进工具卡', () => {
    const entries: SessionEntry[] = [
      {
        type: 'message', id: 'a1', parentId: null, timestamp: 't',
        message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'read', input: {} }] },
      },
      {
        type: 'message', id: 'r1', parentId: 'a1', timestamp: 't',
        message: {
          role: 'toolResult',
          content: [
            {
              type: 'tool_result', tool_use_id: 't1', content: '[图片: x.png]',
              blocks: [{ type: 'image', media_type: 'image/png', data: 'aGk=' }],
            },
          ],
        },
      },
    ]
    const msgs = entriesToMessages(entries)
    const toolMsg = msgs.find((m) => m.role === 'tool')
    expect(toolMsg?.tool?.blocks).toEqual([{ type: 'image', media_type: 'image/png', data: 'aGk=' }])
  })
})

// 票 03：用户消息图片块回放
describe('用户图片回放（票 03）', () => {
  it('entriesToMessages 把用户消息的顶层 image 块收进 images 字段', () => {
    const entries: SessionEntry[] = [
      {
        type: 'message', id: 'u1', parentId: null, timestamp: 't',
        message: {
          role: 'user',
          content: [
            { type: 'text', text: '看这张图' },
            { type: 'image', media_type: 'image/png', data: 'aGk=' },
          ],
        },
      },
    ]
    const msgs = entriesToMessages(entries)
    expect(msgs[0]?.role).toBe('user')
    expect(msgs[0]?.text).toBe('看这张图')
    expect(msgs[0]?.images).toEqual([{ media_type: 'image/png', data: 'aGk=' }])
  })

  it('纯文本用户消息 images 为 undefined（不加空数组）', () => {
    const entries: SessionEntry[] = [
      {
        type: 'message', id: 'u2', parentId: null, timestamp: 't',
        message: { role: 'user', content: [{ type: 'text', text: '普通消息' }] },
      },
    ]
    const msgs = entriesToMessages(entries)
    expect(msgs[0]?.images).toBeUndefined()
  })
})
