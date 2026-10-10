import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AgentEvent, ChatUsage } from '@/api/ws'
import {
  deleteSession as deleteSessionApi,
  fetchModels,
  fetchSessionBranch,
  fetchSessionEntries,
  fetchSessions,
  fetchSkills,
  fetchUsage,
  type SkillInfo,
  type ModelInfo,
  type SessionContentBlock,
  type SessionEntry,
  type SessionSummary,
  type UsageTotals,
} from '@/api/rest'
import { useAuthStore } from '@/stores/auth'
import { useConnectionStore } from '@/stores/connection'

/** 工具结果富块（票 24）：与后端 tool_result.blocks 对齐。 */
export interface ToolResultRichBlock {
  type: string
  text?: string
  media_type?: string
  data?: string
}

/** 工具卡片的界面态（三态：running / done / error）。 */
export interface ToolCallView {
  toolCallId: string
  name: string
  params: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  content: string
  /** 执行中的进度增量（tool_execution_update），渲染时取最新一条 */
  updates: string[]
  /** 富结果块（票 24）：text/image；缺省无 = 存量纯文本形态 */
  blocks?: ToolResultRichBlock[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'error' | 'notice'
  text: string
  thinking: string
  status: 'streaming' | 'done' | 'aborted' | 'error'
  usage?: ChatUsage | null
  /** role === 'tool' 时存在 */
  tool?: ToolCallView
  /** 历史回放消息对应的 Session 条目 id（分叉入口用；流式消息没有） */
  entryId?: string
  /** 思考耗时（秒）——第一条 thinking delta 到 message_end 的墙钟差 */
  thinkSeconds?: number
  /** 内部：思考开始时间戳（message_end 时换算 thinkSeconds） */
  thinkStartedAt?: number
  /** 分支注记（票 17）：分叉点消息携带，前端据此渲染切换 pager */
  branch?: { index: number; total: number; siblings: string[] }
}

let userSeq = 0
let errorSeq = 0
let noticeSeq = 0

/** Session 条目 → 界面消息（历史回放）：tool_use 建卡片，toolResult 回填结果。 */
export function entriesToMessages(entries: SessionEntry[]): ChatMessage[] {
  const out: ChatMessage[] = []
  const toolCards = new Map<string, ToolCallView>()
  for (const e of entries) {
    if (e.type === 'compaction') {
      out.push({
        id: `notice_${e.id}`,
        role: 'notice',
        text: '此处之前的对话已压缩为摘要（重放从摘要处续接）。',
        thinking: '',
        status: 'done',
      })
      continue
    }
    if (e.type === 'usage') {
      const last = out[out.length - 1]
      if (last && last.role === 'assistant') last.usage = e.usage ?? null
      continue
    }
    if (e.type !== 'message' || !e.message) continue
    const { role, content } = e.message
    // 分支注记（票 17）：分叉点的消息携带，MessageList 据以渲染 pager
    const branch =
      e.branch_total != null
        ? { index: e.branch_index ?? 0, total: e.branch_total, siblings: e.siblings ?? [] }
        : undefined
    if (role === 'user') {
      out.push({
        id: e.id,
        entryId: e.id,
        role: 'user',
        text: joinBlocks(content, 'text'),
        thinking: '',
        status: 'done',
        branch,
      })
    } else if (role === 'assistant') {
      out.push({
        id: e.id,
        entryId: e.id,
        role: 'assistant',
        text: joinBlocks(content, 'text'),
        thinking: joinBlocks(content, 'thinking'),
        status: 'done',
        branch,
      })
      for (const b of content) {
        if (b.type === 'tool_use' && b.id) {
          const card: ToolCallView = {
            toolCallId: b.id,
            name: b.name ?? '',
            params: b.input ?? {},
            status: 'done',
            content: '',
            updates: [],
          }
          toolCards.set(b.id, card)
          out.push({ id: `${e.id}_${b.id}`, role: 'tool', text: '', thinking: '', status: 'done', tool: card })
        }
      }
    } else if (role === 'toolResult') {
      for (const b of content) {
        if (b.type === 'tool_result') {
          const card = toolCards.get(b.tool_use_id ?? '')
          if (card) {
            card.content = b.content ?? ''
            card.blocks = (b as { blocks?: ToolResultRichBlock[] }).blocks
            card.status = b.is_error ? 'error' : 'done'
          }
        }
      }
    }
  }
  return out
}

function joinBlocks(blocks: SessionContentBlock[], kind: 'text' | 'thinking'): string {
  return blocks
    .filter((b) => b.type === kind && typeof b[kind] === 'string')
    .map((b) => b[kind])
    .join('')
}

export interface PendingApproval {
  approvalId: string
  tool: string
  params: Record<string, unknown>
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const running = ref(false)
  const pendingCount = ref(0)
  const pendingApproval = ref<PendingApproval | null>(null)
  const currentSessionId = ref<string | null>(null)
  const sessions = ref<SessionSummary[]>([])
  // 模型与 thinking level（ticket 14）：会话级，切换对下一 run 生效
  const models = ref<ModelInfo[]>([])
  const currentModel = ref<string>('') // 空串 = 尚未确定（等 session_info 同步）
  const currentEffort = ref<'low' | 'medium' | 'high' | 'xhigh' | 'max'>('high')
  const usage = ref<UsageTotals | null>(null)
  // 手动选技能（Claude Code 式）：菜单数据 + 当前选中（随下一条消息发送，发送后清除）
  const skills = ref<SkillInfo[]>([])
  const selectedSkill = ref<string>('')

  function byId(id: string): ChatMessage | undefined {
    return messages.value.find((m) => m.id === id)
  }

  function pushError(message: string): void {
    messages.value.push({
      id: `err_${++errorSeq}`,
      role: 'error',
      text: message,
      thinking: '',
      status: 'error',
    })
  }

  /** 拉取并回放一个 Session 的完整历史。 */
  async function loadEntries(sessionId: string): Promise<void> {
    if (running.value) return // 流进行中不覆盖
    const token = useAuthStore().token
    if (!token) return
    try {
      const entries = await fetchSessionEntries(token, sessionId)
      messages.value = entriesToMessages(entries)
    } catch {
      pushError('历史会话加载失败')
    }
  }

  /** WS 下行事件入口（connection.setEventHandler 转交到这里）。 */
  function handleEvent(raw: Record<string, unknown>): void {
    // 下行即本协议（类型手写、与后端 app/chat.py 对齐，spec §5）
    const ev = raw as unknown as AgentEvent
    if (ev.type === 'message_start') {
      messages.value.push({
        id: ev.msg_id,
        role: 'assistant',
        text: '',
        thinking: '',
        status: 'streaming',
      })
    } else if (ev.type === 'message_update') {
      const msg = byId(ev.msg_id)
      if (!msg) return
      if (ev.delta.type === 'thinking') {
        if (!msg.thinking && !msg.thinkStartedAt) msg.thinkStartedAt = Date.now()
        msg.thinking += ev.delta.text
      } else msg.text += ev.delta.text
    } else if (ev.type === 'message_end') {
      const msg = byId(ev.msg_id)
      if (msg) {
        if (msg.thinkStartedAt) {
          msg.thinkSeconds = Math.max(1, Math.round((Date.now() - msg.thinkStartedAt) / 1000))
        }
        msg.usage = ev.usage ?? null
        msg.status = ev.stop_reason === 'aborted' ? 'aborted' : 'done'
      }
      // abort 路径没有 agent_end，由 aborted 收尾关运行态；
      // 正常多轮 run 里每轮 message_end 之后还有工具相位与新 Turn，不在此关
      if (ev.stop_reason === 'aborted') running.value = false
    } else if (ev.type === 'tool_execution_start') {
      messages.value.push({
        id: `tool_${ev.tool_call_id}`,
        role: 'tool',
        text: '',
        thinking: '',
        status: 'streaming',
        tool: {
          toolCallId: ev.tool_call_id,
          name: ev.name,
          params: ev.params,
          status: 'running',
          content: '',
          updates: [],
        },
      })
    } else if (ev.type === 'tool_execution_update') {
      const msg = byId(`tool_${ev.tool_call_id}`)
      if (msg?.tool) msg.tool.updates.push(ev.delta)
    } else if (ev.type === 'tool_execution_end') {
      const msg = byId(`tool_${ev.tool_call_id}`)
      if (msg?.tool) {
        msg.tool.status = ev.is_error ? 'error' : 'done'
        msg.tool.content = ev.content
        msg.tool.blocks = (ev as { blocks?: ToolResultRichBlock[] }).blocks
        msg.status = ev.is_error ? 'error' : 'done'
      }
    } else if (ev.type === 'agent_end') {
      running.value = false
    } else if (ev.type === 'queue_update') {
      pendingCount.value = ev.pending
    } else if (ev.type === 'approval_request') {
      // 幂等：断线重连后服务端按同一 approval_id 重发，已弹窗的不重复弹
      const existing = pendingApproval.value
      if (!existing || existing.approvalId !== ev.approval_id) {
        pendingApproval.value = { approvalId: ev.approval_id, tool: ev.tool, params: ev.params }
      }
    } else if (ev.type === 'approval_resolved') {
      if (pendingApproval.value?.approvalId === ev.approval_id) {
        pendingApproval.value = null
      }
    } else if (ev.type === 'compaction_end') {
      if (ev.compacted) {
        messages.value.push({
          id: `notice_${++noticeSeq}`,
          role: 'notice',
          text: '会话已压缩：早期对话已替换为摘要，长对话可无缝继续。',
          thinking: '',
          status: 'done',
        })
      }
    } else if (ev.type === 'session_info') {
      currentSessionId.value = ev.session_id
      if (ev.model) currentModel.value = ev.model // 新会话/切换会话时同步后端的模型继承
      void loadEntries(ev.session_id)
      void loadSessions()
    } else if (ev.type === 'error') {
      const streaming = messages.value.find((m) => m.status === 'streaming')
      if (streaming) streaming.status = 'error'
      pushError(ev.message)
      running.value = false
    }
  }

  function sendPrompt(text: string, skill?: string): void {
    const trimmed = text.trim()
    if (!trimmed) return
    messages.value.push({
      id: `user_${++userSeq}`,
      role: 'user',
      text: trimmed,
      thinking: '',
      status: 'done',
    })
    // 运行中发送 → WS 侧入队（Steer：当前工具轮后注入 / Follow-up：loop 结束后续跑）
    const wasRunning = running.value
    if (!wasRunning) running.value = true
    const usedSkill = skill ?? selectedSkill.value
    if (usedSkill) selectedSkill.value = '' // 选择一次性：随本条消息消耗
    const client = useConnectionStore().getClient()
    if (!client) {
      if (!wasRunning) running.value = false
      pushError('WS 未连接——请刷新页面重新登录')
      return
    }
    client
      .send('prompt', { text: trimmed, ...(usedSkill ? { skill: usedSkill } : {}) })
      .then((resp) => {
        if (resp.ok === false) {
          if (!wasRunning) running.value = false
          pushError(String(resp.error ?? '发送被拒绝'))
        }
      })
      .catch(() => {
        if (!wasRunning) running.value = false
        pushError('连接已断开，请稍后重试')
      })
  }

  function abort(): void {
    useConnectionStore()
      .getClient()
      ?.send('abort')
      .catch(() => {})
  }

  /** 审批裁决（ADR-0001）：允许/拒绝后清掉弹窗（approval_resolved 事件兜底清）。 */
  function approve(allow: boolean): void {
    const current = pendingApproval.value
    if (!current) return
    pendingApproval.value = null
    useConnectionStore()
      .getClient()
      ?.send('approve', { approval_id: current.approvalId, allow })
      .catch(() => {})
  }

  /** 新开对话即新 Session；空历史随 session_info 事件到达后覆盖消息区。 */
  function newSession(): void {
    if (running.value) return
    useConnectionStore()
      .getClient()
      ?.send('new_session')
      .catch(() => {})
  }

  // ---- 会话管理（ticket 09） ----

  async function loadSessions(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      sessions.value = await fetchSessions(token)
    } catch {
      // 侧栏加载失败不打断聊天；下次打开重试
    }
  }

  /** 切换会话：WS 命令 → session_info 事件触发回放。 */
  function switchSession(sessionId: string): void {
    if (running.value) return
    useConnectionStore()
      .getClient()
      ?.send('switch_session', { session_id: sessionId })
      .then((resp) => {
        if (resp.ok === false) pushError(String(resp.error ?? '切换失败'))
      })
      .catch(() => pushError('连接已断开，无法切换会话'))
  }

  /** 删除会话；删的是当前会话则顺带新开一个（避免悬空引用）。 */
  async function deleteSession(sessionId: string): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      await deleteSessionApi(token, sessionId)
    } catch {
      pushError('会话删除失败')
      return
    }
    await loadSessions()
    if (currentSessionId.value === sessionId) {
      messages.value = []
      currentSessionId.value = null
      // 删的是当前会话：优先切到剩余的最近会话，只有全删光才新建——
      // 否则每次删除都会立刻冒出一个新的空会话，看起来像"删不掉"
      const next = sessions.value[0]
      if (next) switchSession(next.id)
      else newSession()
    }
  }

  /** 切换到指定分支（票 17）：拉取该分支视图回放，活跃分支跟随（切换即激活，续写落新叶）。 */
  async function switchBranch(siblingId: string): Promise<void> {
    const sessionId = currentSessionId.value
    if (!sessionId || running.value) return
    const token = useAuthStore().token
    if (!token) return
    try {
      const entries = await fetchSessionBranch(token, sessionId, siblingId)
      messages.value = entriesToMessages(entries)
      // 活跃分支跟随视图：WS branch 落到该分支的会话叶（usage 等元数据条目跳过）
      const convEntries = entries.filter((e) => e.type === 'message' || e.type === 'compaction')
      const leaf = convEntries[convEntries.length - 1]
      if (leaf) {
        useConnectionStore()
          .getClient()
          ?.send('branch', { entry_id: leaf.id })
          .catch(() => {})
      }
    } catch {
      pushError('分支切换失败')
    }
  }

  /** 从历史条目分叉（ADR-0002）：服务端记分叉点，本地把视图截到该条目。 */
  function branch(entryId: string): void {
    if (running.value) return
    const idx = messages.value.findIndex((m) => m.entryId === entryId)
    if (idx < 0) return
    messages.value = messages.value.slice(0, idx + 1)
    useConnectionStore()
      .getClient()
      ?.send('branch', { entry_id: entryId })
      .catch(() => {})
  }

  // ---- 模型 / thinking level / 用量（ticket 14） ----

  async function loadModels(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      models.value = await fetchModels(token)
    } catch {
      // 模型列表拉取失败不打断聊天
    }
  }

  async function loadUsage(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      usage.value = await fetchUsage(token)
    } catch {
      // 用量拉取失败不打断聊天
    }
  }

  /** 拉取可用技能清单（Composer 技能菜单）；失败静默。 */
  async function loadSkills(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      skills.value = await fetchSkills(token)
    } catch {
      skills.value = []
    }
  }

  /** 切换会话模型（对下一 run 生效）。 */
  function setModel(modelId: string): void {
    useConnectionStore()
      .getClient()
      ?.send('set_model', { model: modelId })
      .then((resp) => {
        if (resp.ok === false) pushError(String(resp.error ?? '模型切换失败'))
        else currentModel.value = modelId
      })
      .catch(() => pushError('连接已断开，无法切换模型'))
  }

  /** 切换 thinking level（low/medium/high/xhigh/max）。 */
  function setThinkingLevel(level: 'low' | 'medium' | 'high' | 'xhigh' | 'max'): void {
    useConnectionStore()
      .getClient()
      ?.send('set_thinking_level', { level })
      .then((resp) => {
        if (resp.ok === false) pushError(String(resp.error ?? '切换失败'))
        else currentEffort.value = level
      })
      .catch(() => pushError('连接已断开，无法切换'))
  }

  function reset(): void {
    messages.value = []
    running.value = false
    pendingCount.value = 0
    pendingApproval.value = null
    currentSessionId.value = null
    sessions.value = []
    models.value = []
    currentModel.value = ''
    currentEffort.value = 'high'
    usage.value = null
    skills.value = []
    selectedSkill.value = ''
  }

  return {
    messages,
    running,
    pendingCount,
    pendingApproval,
    currentSessionId,
    sessions,
    models,
    currentModel,
    currentEffort,
    usage,
    skills,
    selectedSkill,
    handleEvent,
    sendPrompt,
    loadSkills,
    abort,
    approve,
    newSession,
    loadSessions,
    switchSession,
    deleteSession,
    branch,
    switchBranch,
    loadModels,
    loadUsage,
    setModel,
    setThinkingLevel,
    reset,
  }
})
