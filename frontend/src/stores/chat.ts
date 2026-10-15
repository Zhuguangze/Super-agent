import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
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
  /** 用户消息携带的图片（多模态输入）；扁平字典形状与工具富块一致 */
  images?: { media_type: string; data: string }[]
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
      // 票 03：顶层 image 块 → images（多模态回放）
      const images = content
        .filter((b) => b.type === 'image')
        .map((b) => ({ media_type: b.media_type ?? '', data: b.data ?? '' }))
      out.push({
        id: e.id,
        entryId: e.id,
        role: 'user',
        text: joinBlocks(content, 'text'),
        thinking: '',
        status: 'done',
        images: images.length ? images : undefined,
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
  /** 审批来源会话（ADR-0005：并发会话下审批的归属） */
  sessionId?: string
}

export const useChatStore = defineStore('chat', () => {
  // ---- 多会话并发（ADR-0005）：消息/运行态/审批全部按会话分桶 ----
  const messagesBySession = ref<Record<string, ChatMessage[]>>({})
  const runningBySession = ref<Record<string, boolean>>({})
  const pendingBySession = ref<Record<string, number>>({})
  const turnBySession = ref<Record<string, number>>({})
  const unreadBySession = ref<Record<string, boolean>>({})
  const pendingApprovals = ref<PendingApproval[]>([])
  const currentSessionId = ref<string | null>(null)
  const sessions = ref<SessionSummary[]>([])

  /** 当前会话的消息视图（渲染绑定；写入经 bufferFor 路由）。无会话时回落临时桶（hero 首发）。 */
  const messages = computed<ChatMessage[]>(() => {
    const id = currentSessionId.value ?? '__pending__'
    return messagesBySession.value[id] ?? []
  })
  /** 当前会话是否有活动 run */
  const running = computed<boolean>(() => runningBySession.value[currentSessionId.value ?? '__pending__'] ?? false)
  /** 当前会话的 steer/follow-up 排队数 */
  const pendingCount = computed<number>(
    () => pendingBySession.value[currentSessionId.value ?? '__pending__'] ?? 0,
  )
  const turnCount = computed<number>(
    () => turnBySession.value[currentSessionId.value ?? '__pending__'] ?? 0,
  )
  /** 全局最早的未决审批（任意会话,Q4-B）:弹窗标注来源,避免后台审批被超时误拒 */
  const pendingApproval = computed<PendingApproval | null>(() => pendingApprovals.value[0] ?? null)

  /** 取（或建）某个会话的消息桶。无会话 id（hero 首发未回 id）进临时桶。 */
  function bufferFor(sessionId: string | null): ChatMessage[] {
    const key = sessionId ?? '__pending__'
    let buf = messagesBySession.value[key]
    if (!buf) {
      buf = []
      messagesBySession.value = { ...messagesBySession.value, [key]: buf }
    }
    return buf
  }

  /** 事件归属会话：协议带 session_id；缺失时回落当前会话（旧事件兼容） */
  function sessionOf(ev: Record<string, unknown>): string | null {
    return (ev.session_id as string | undefined) ?? currentSessionId.value
  }

  function setRunning(sessionId: string | null, value: boolean): void {
    const key = sessionId ?? '__pending__'
    runningBySession.value = { ...runningBySession.value, [key]: value }
  }
  // 模型与 thinking level（ticket 14）：会话级，切换对下一 run 生效
  const models = ref<ModelInfo[]>([])
  const currentModel = ref<string>('') // 空串 = 尚未确定（等 session_info 同步）
  const currentEffort = ref<'low' | 'medium' | 'high' | 'xhigh' | 'max'>('high')
  const usage = ref<UsageTotals | null>(null)
  // 手动选技能（Claude Code 式）：菜单数据 + 当前选中（随下一条消息发送，发送后清除）
  const skills = ref<SkillInfo[]>([])

  // ---- 本地持久化：专家铭牌按会话存、技能选择全局存，刷新页面不丢 ----
  const EXPERTS_KEY = 'super_agent_experts'
  const SKILL_KEY = 'super_agent_selected_skill'

  function loadJSON<T>(key: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(key)
      return raw ? (JSON.parse(raw) as T) : fallback
    } catch {
      return fallback
    }
  }

  function saveJSON(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* 存储不可用时静默跳过 */
    }
  }

  const selectedSkill = ref<string>(loadJSON<string>(SKILL_KEY, ''))
  watch(selectedSkill, (v) => saveJSON(SKILL_KEY, v))

  // ---- 专家模式（空会话专家卡）：会话 id → 专家铭牌，钉在输入框上跟随会话 ----
  // persona 随每条消息作为 expert 字段发给后端，注入 system prompt
  const expertBySession = ref<Record<string, { label: string; hue: string; persona: string }>>(
    loadJSON(EXPERTS_KEY, {}),
  )
  watch(expertBySession, (v) => saveJSON(EXPERTS_KEY, v))
  const pendingExpert = ref<{ label: string; hue: string; persona: string } | null>(null) // hero 直发首条时会话 id 未回，先暂存（不持久化：尚未钉到任何会话）
  const activeExpert = computed(() => {
    const id = currentSessionId.value
    return (id && expertBySession.value[id]) || null
  })

  function beginExpertSession(expert: { label: string; hue: string; persona: string }): void {
    const id = currentSessionId.value
    if (id) expertBySession.value = { ...expertBySession.value, [id]: expert }
    else pendingExpert.value = expert
  }

  // 新建会话的 id 由 session_info 事件带回，此时把暂存的专家落到该会话
  watch(currentSessionId, (id) => {
    if (!id || !pendingExpert.value) return
    expertBySession.value = { ...expertBySession.value, [id]: pendingExpert.value }
    pendingExpert.value = null
  })

  // 进入会话即清除其未读标记（Q3-B）
  watch(currentSessionId, (id) => {
    if (id && unreadBySession.value[id]) {
      unreadBySession.value = { ...unreadBySession.value, [id]: false }
    }
  })

  function byId(sessionId: string | null, id: string): ChatMessage | undefined {
    const key = sessionId ?? '__pending__'
    return messagesBySession.value[key]?.find((m) => m.id === id)
  }

  function pushError(message: string, sessionId: string | null = currentSessionId.value): void {
    bufferFor(sessionId).push({
      id: `err_${++errorSeq}`,
      role: 'error',
      text: message,
      thinking: '',
      status: 'error',
    })
  }

  /** 拉取并回放一个 Session 的完整历史。 */
  async function loadEntries(sessionId: string): Promise<void> {
    if (runningBySession.value[sessionId]) return // 该会话有活动 run：活缓冲优先，不覆盖
    // 桶缓存（切换卡顿优化 Q7-B）：该会话此前看过且经事件实时更新，桶内即最新，不重拉
    if ((messagesBySession.value[sessionId]?.length ?? 0) > 0) return
    const token = useAuthStore().token
    if (!token) return
    try {
      const entries = await fetchSessionEntries(token, sessionId)
      messagesBySession.value = { ...messagesBySession.value, [sessionId]: entriesToMessages(entries) }
    } catch {
      pushError('历史会话加载失败', sessionId)
    }
  }

  // ---- 流式 delta 合帧（切换卡顿优化 Q7-A）：message_update 按 ~100ms 批量应用，降低主线程占用 ----
  interface PendingDelta {
    sid: string | null
    thinking: string
    text: string
    thinkStart: boolean
  }
  const pendingDeltas = new Map<string, PendingDelta>()
  let deltaTimer: number | undefined

  function flushDeltas(msgId?: string): void {
    const apply = (id: string, p: PendingDelta) => {
      const msg = byId(p.sid, id)
      if (!msg) return
      if (p.thinkStart && !msg.thinkStartedAt) msg.thinkStartedAt = Date.now()
      msg.thinking += p.thinking
      msg.text += p.text
    }
    if (msgId !== undefined) {
      const p = pendingDeltas.get(msgId)
      if (p) {
        apply(msgId, p)
        pendingDeltas.delete(msgId)
      }
    } else {
      for (const [id, p] of pendingDeltas) apply(id, p)
      pendingDeltas.clear()
    }
    if (pendingDeltas.size === 0 && deltaTimer !== undefined) {
      clearInterval(deltaTimer)
      deltaTimer = undefined
    }
  }

  /** WS 下行事件入口（connection.setEventHandler 转交到这里）。 */
  function handleEvent(raw: Record<string, unknown>): void {
    // 下行即本协议（类型手写、与后端 app/chat.py 对齐，spec §5）
    const ev = raw as unknown as AgentEvent
    const sid = sessionOf(raw)
    const buf = bufferFor(sid)
    if (ev.type === 'message_start') {
      buf.push({
        id: ev.msg_id,
        role: 'assistant',
        text: '',
        thinking: '',
        status: 'streaming',
      })
    } else if (ev.type === 'message_update') {
      // 合帧：先攒待应用增量，定时器统一冲刷（token 级 delta 不再逐条驱动渲染）
      const p = pendingDeltas.get(ev.msg_id) ?? { sid, thinking: '', text: '', thinkStart: false }
      pendingDeltas.set(ev.msg_id, p)
      if (ev.delta.type === 'thinking') {
        p.thinking += ev.delta.text
        p.thinkStart = true
      } else {
        p.text += ev.delta.text
      }
      if (deltaTimer === undefined) {
        deltaTimer = window.setInterval(() => flushDeltas(), 100)
      }
    } else if (ev.type === 'message_end') {
      flushDeltas(ev.msg_id) // 收尾前冲刷该消息的待应用增量，保证正文完整
      const msg = byId(sid, ev.msg_id)
      if (msg) {
        if (msg.thinkStartedAt) {
          msg.thinkSeconds = Math.max(1, Math.round((Date.now() - msg.thinkStartedAt) / 1000))
        }
        msg.usage = ev.usage ?? null
        msg.status = ev.stop_reason === 'aborted' ? 'aborted' : 'done'
      }
      // abort 路径没有 agent_end，由 aborted 收尾关运行态；
      // 正常多轮 run 里每轮 message_end 之后还有工具相位与新 Turn，不在此关
      if (ev.stop_reason === 'aborted') setRunning(sid, false)
    } else if (ev.type === 'tool_execution_start') {
      buf.push({
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
      const msg = byId(sid, `tool_${ev.tool_call_id}`)
      if (msg?.tool) msg.tool.updates.push(ev.delta)
    } else if (ev.type === 'tool_execution_end') {
      const msg = byId(sid, `tool_${ev.tool_call_id}`)
      if (msg?.tool) {
        msg.tool.status = ev.is_error ? 'error' : 'done'
        msg.tool.content = ev.content
        msg.tool.blocks = (ev as { blocks?: ToolResultRichBlock[] }).blocks
        msg.status = ev.is_error ? 'error' : 'done'
      }
      // truncated guard：模型输出被截断，工具未执行，模型即将重发完整内容
      if (ev.is_error && ev.content.includes('截断')) {
        buf.push({
          id: `notice_${++noticeSeq}`,
          role: 'notice',
          text: '模型输出被截断，工具未执行——正在重发完整内容。',
          thinking: '',
          status: 'done',
        })
      }
    } else if (ev.type === 'agent_start') {
      turnBySession.value = { ...turnBySession.value, [sid ?? '__pending__']: 0 }
    } else if (ev.type === 'turn_start') {
      turnBySession.value = { ...turnBySession.value, [sid ?? '__pending__']: ev.turn }
    } else if (ev.type === 'compaction_start') {
      buf.push({
        id: `notice_${++noticeSeq}`,
        role: 'notice',
        text: '正在压缩上下文…',
        thinking: '',
        status: 'done',
      })
    } else if (ev.type === 'agent_end') {
      setRunning(sid, false)
      // 后台会话完成 → 未读标记，进入该会话时清除（Q3-B）
      if (sid && sid !== currentSessionId.value) {
        unreadBySession.value = { ...unreadBySession.value, [sid]: true }
      }
    } else if (ev.type === 'queue_update') {
      const key = sid ?? '__pending__'
      pendingBySession.value = { ...pendingBySession.value, [key]: ev.pending }
    } else if (ev.type === 'approval_request') {
      // 幂等：断线重连后服务端按同一 approval_id 重发，已弹窗的不重复弹
      if (!pendingApprovals.value.some((p) => p.approvalId === ev.approval_id)) {
        pendingApprovals.value = [
          ...pendingApprovals.value,
          {
            approvalId: ev.approval_id,
            tool: ev.tool,
            params: ev.params,
            sessionId: (raw.session_id as string | undefined) ?? undefined,
          },
        ]
      }
    } else if (ev.type === 'approval_resolved') {
      pendingApprovals.value = pendingApprovals.value.filter((p) => p.approvalId !== ev.approval_id)
    } else if (ev.type === 'compaction_end') {
      if (ev.compacted) {
        buf.push({
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
      const streaming = buf.find((m) => m.status === 'streaming')
      if (streaming) streaming.status = 'error'
      pushError(ev.message, sid)
      setRunning(sid, false)
    }
  }

  function sendPrompt(
    text: string,
    skill?: string,
    opts?: { hidden?: boolean; images?: { media_type: string; data: string }[] },
  ): void {
    const trimmed = text.trim()
    if (!trimmed && !opts?.images?.length) return
    if (!opts?.hidden) {
      bufferFor(currentSessionId.value).push({
        id: `user_${++userSeq}`,
        role: 'user',
        text: trimmed,
        thinking: '',
        status: 'done',
        images: opts?.images,
      })
    }
    // 运行中发送 → WS 侧入队（Steer：当前工具轮后注入 / Follow-up：loop 结束后续跑）
    const wasRunning = running.value
    if (!wasRunning) setRunning(currentSessionId.value, true)
    const usedSkill = skill ?? selectedSkill.value
    if (usedSkill) selectedSkill.value = '' // 选择一次性：随本条消息消耗
    const client = useConnectionStore().getClient()
    if (!client) {
      if (!wasRunning) setRunning(currentSessionId.value, false)
      pushError('WS 未连接——请刷新页面重新登录')
      return
    }
    const expertBadge = activeExpert.value ?? pendingExpert.value // 首条消息时 id 未回，用暂存铭牌
    client
      .send('prompt', {
        text: trimmed,
        ...(usedSkill ? { skill: usedSkill } : {}),
        ...(expertBadge ? { expert: expertBadge.persona } : {}),
        ...(opts?.hidden ? { hidden: true } : {}), // 触发词不入库：回放/切会话时不再出现
        ...(opts?.images?.length ? { images: opts.images } : {}), // 多模态：图片随消息上行
      })
      .then((resp) => {
        if (resp.ok === false) {
          if (!wasRunning) setRunning(currentSessionId.value, false)
          pushError(String(resp.error ?? '发送被拒绝'))
        }
      })
      .catch(() => {
        if (!wasRunning) setRunning(currentSessionId.value, false)
        pushError('连接已断开，请稍后重试')
      })
  }

  function abort(): void {
    // 定点中止当前会话的 run（ADR-0005）；其他会话的 run 不受影响
    useConnectionStore()
      .getClient()
      ?.send('abort', { session_id: currentSessionId.value ?? undefined })
      .catch(() => {})
  }

  /** 审批裁决（ADR-0001）：允许/拒绝后从队列移除（approval_resolved 事件兜底清）。 */
  function approve(allow: boolean): void {
    const current = pendingApproval.value
    if (!current) return
    pendingApprovals.value = pendingApprovals.value.filter((p) => p.approvalId !== current.approvalId)
    useConnectionStore()
      .getClient()
      ?.send('approve', { approval_id: current.approvalId, allow })
      .catch(() => {})
  }

  /** 新开对话即新 Session；空历史随 session_info 事件到达后覆盖消息区。运行中也可开新会话（ADR-0005）。 */
  function newSession(): void {
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
      // 只保留仍存在的会话的专家铭牌，防止 localStorage 无限增长
      const alive = new Set(sessions.value.map((s) => s.id))
      const stale = Object.keys(expertBySession.value).filter((id) => !alive.has(id))
      if (stale.length) {
        const next = { ...expertBySession.value }
        for (const id of stale) delete next[id]
        expertBySession.value = next
      }
    } catch {
      // 侧栏加载失败不打断聊天；下次打开重试
    }
  }

  /** 切换会话：WS 命令 → session_info 事件触发回放。运行中切换不受限（ADR-0005）。 */
  function switchSession(sessionId: string): void {
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
    delete expertBySession.value[sessionId]
    await loadSessions()
    if (currentSessionId.value === sessionId) {
      messagesBySession.value[sessionId] = []
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
    if (!sessionId || runningBySession.value[sessionId]) return // 该会话运行中：不覆盖流式缓冲
    const token = useAuthStore().token
    if (!token) return
    try {
      const entries = await fetchSessionBranch(token, sessionId, siblingId)
      messagesBySession.value = { ...messagesBySession.value, [sessionId]: entriesToMessages(entries) }
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
      pushError('分支切换失败', sessionId)
    }
  }

  /** 从历史条目分叉（ADR-0002）：服务端记分叉点，本地把视图截到该条目。 */
  function branch(entryId: string): void {
    const sessionId = currentSessionId.value
    if (!sessionId || runningBySession.value[sessionId]) return // 该会话运行中：拒绝分叉（Q2-B）
    const idx = (messagesBySession.value[sessionId] ?? []).findIndex((m) => m.entryId === entryId)
    if (idx < 0) return
    messagesBySession.value = {
      ...messagesBySession.value,
      [sessionId]: (messagesBySession.value[sessionId] ?? []).slice(0, idx + 1),
    }
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
    messagesBySession.value = {}
    runningBySession.value = {}
    pendingBySession.value = {}
    unreadBySession.value = {}
    pendingApprovals.value = []
    pendingDeltas.clear()
    if (deltaTimer !== undefined) {
      clearInterval(deltaTimer)
      deltaTimer = undefined
    }
    currentSessionId.value = null
    sessions.value = []
    models.value = []
    currentModel.value = ''
    currentEffort.value = 'high'
    usage.value = null
    skills.value = []
    selectedSkill.value = ''
    expertBySession.value = {}
    pendingExpert.value = null
    try {
      localStorage.removeItem(EXPERTS_KEY)
      localStorage.removeItem(SKILL_KEY)
    } catch {
      /* 同上 */
    }
  }

  return {
    messages,
    messagesBySession,
    running,
    runningBySession,
    unreadBySession,
    pendingCount,
    turnCount,
    pendingApproval,
    currentSessionId,
    sessions,
    models,
    currentModel,
    currentEffort,
    usage,
    skills,
    selectedSkill,
    activeExpert,
    expertBySession,
    beginExpertSession,
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
