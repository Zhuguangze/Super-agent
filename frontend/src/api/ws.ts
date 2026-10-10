/** WS 客户端——连接管理、命令 req_id 关联、断线重连（spec §4.2 命令-响应模型）。 */

export type WsStatus =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'unauthorized'

export interface WsClientOptions {
  /** 显式指定 WS 地址（默认从当前页面推导 /ws） */
  url?: string
  /** 重连间隔毫秒数 */
  delayMs?: number
}

type Pending = {
  resolve: (value: Record<string, unknown>) => void
  reject: (reason: Error) => void
}

// ---- 下行事件（spec §4.1 的 v1 子集，与后端 app/chat.py 一一对应） ----

export interface ChatUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface MessageStartEvent {
  type: 'message_start'
  msg_id: string
  role: string
}

export interface MessageUpdateEvent {
  type: 'message_update'
  msg_id: string
  delta: { type: 'text' | 'thinking'; text: string }
}

export interface MessageEndEvent {
  type: 'message_end'
  msg_id: string
  stop_reason: string | null
  usage?: ChatUsage | null
}

export interface ErrorEvent {
  type: 'error'
  code: string
  message: string
}

export interface SessionInfoEvent {
  type: 'session_info'
  session_id: string
  /** 该会话当前模型（新会话继承上次使用的模型，前端同步显示） */
  model?: string
}

// ---- Agent 相位事件（ticket 05，agent/loop.py 发出） ----

export interface AgentStartEvent {
  type: 'agent_start'
}

export interface TurnStartEvent {
  type: 'turn_start'
  turn: number
}

export interface TurnEndEvent {
  type: 'turn_end'
  turn: number
  stop_reason: string
}

export interface AgentEndEvent {
  type: 'agent_end'
  stop_reason: string
  usage: ChatUsage
}

export interface ToolExecutionStartEvent {
  type: 'tool_execution_start'
  tool_call_id: string
  name: string
  params: Record<string, unknown>
}

export interface ToolExecutionUpdateEvent {
  type: 'tool_execution_update'
  tool_call_id: string
  delta: string
}

export interface ToolExecutionEndEvent {
  type: 'tool_execution_end'
  tool_call_id: string
  content: string
  is_error: boolean
}

export interface QueueUpdateEvent {
  type: 'queue_update'
  pending: number
}

// ---- 压缩（ticket 10） ----

export interface CompactionStartEvent {
  type: 'compaction_start'
}

export interface CompactionEndEvent {
  type: 'compaction_end'
  compacted: boolean
  summary_chars?: number
}

// ---- 审批（ADR-0001，ticket 07） ----

export interface ApprovalRequestEvent {
  type: 'approval_request'
  approval_id: string
  tool: string
  params: Record<string, unknown>
}

export interface ApprovalResolvedEvent {
  type: 'approval_resolved'
  approval_id: string
  allow: boolean
}

export type AgentEvent =
  | MessageStartEvent
  | MessageUpdateEvent
  | MessageEndEvent
  | ErrorEvent
  | SessionInfoEvent
  | AgentStartEvent
  | TurnStartEvent
  | TurnEndEvent
  | AgentEndEvent
  | ToolExecutionStartEvent
  | ToolExecutionUpdateEvent
  | ToolExecutionEndEvent
  | QueueUpdateEvent
  | ApprovalRequestEvent
  | ApprovalResolvedEvent
  | CompactionStartEvent
  | CompactionEndEvent

export class WsClient {
  status: WsStatus = 'disconnected'
  onStatus?: (status: WsStatus) => void
  /** 非 response 的下行数据（事件流，ticket 03 消费） */
  onEvent?: (data: Record<string, unknown>) => void

  private ws: WebSocket | null = null
  private token: string | null = null
  private reqSeq = 0
  private pending = new Map<string, Pending>()
  private everOpened = false

  constructor(private opts: WsClientOptions = {}) {}

  connect(token: string): void {
    this.token = token
    this._open('connecting')
  }

  close(): void {
    this.token = null
    const ws = this.ws
    this.ws = null
    ws?.close()
    this._setStatus('disconnected')
  }

  send(
    cmd: string,
    payload: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('ws not connected'))
    }
    const reqId = String(++this.reqSeq)
    return new Promise((resolve, reject) => {
      this.pending.set(reqId, { resolve, reject })
      this.ws!.send(JSON.stringify({ cmd, req_id: reqId, ...payload }))
    })
  }

  private _open(initial: WsStatus): void {
    if (!this.token) return
    const url = this.opts.url ?? defaultWsUrl()
    this.everOpened = false
    this._setStatus(initial)
    const ws = new WebSocket(`${url}?token=${encodeURIComponent(this.token)}`)
    this.ws = ws
    ws.onopen = () => {
      this.everOpened = true
      this._setStatus('connected')
    }
    ws.onmessage = (ev) => this._handleMessage(String(ev.data))
    ws.onclose = (ev) => this._handleClose(ev.code)
    ws.onerror = () => {} // 错误细节随 onclose 一起到达
  }

  private _handleClose(_code: number): void {
    const hadSocket = this.ws !== null
    this.ws = null
    for (const [, p] of this.pending) p.reject(new Error('connection closed'))
    this.pending.clear()
    if (!hadSocket) return
    if (!this.everOpened) {
      // 握手被拒（真实网络下表现为 HTTP 403）：token 无效 → 停止重连，提示重新登录
      this._setStatus('unauthorized')
      return
    }
    this._setStatus('reconnecting')
    setTimeout(() => this._open('reconnecting'), this.opts.delayMs ?? 1000)
  }

  private _handleMessage(raw: string): void {
    let data: Record<string, unknown>
    try {
      data = JSON.parse(raw)
    } catch {
      return
    }
    const reqId = data.req_id
    if (data.type === 'response' && typeof reqId === 'string' && this.pending.has(reqId)) {
      const p = this.pending.get(reqId)!
      this.pending.delete(reqId)
      p.resolve(data)
      return
    }
    this.onEvent?.(data)
  }

  private _setStatus(s: WsStatus): void {
    this.status = s
    this.onStatus?.(s)
  }
}

function defaultWsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}
