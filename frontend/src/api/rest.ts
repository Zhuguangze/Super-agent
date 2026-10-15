/** REST 客户端——查询类端点（spec §4.3）。写操作走 WS，这里只读。 */

import type { ChatUsage } from '@/api/ws'

export interface SessionContentBlock {
  type: string
  text?: string
  thinking?: string
  signature?: string
  // image（票 01：用户消息多模态图片块）
  media_type?: string
  data?: string
  // tool_use（assistant 发起调用）
  id?: string
  name?: string
  input?: Record<string, unknown>
  // tool_result（toolResult 消息回填）
  tool_use_id?: string
  content?: string
  is_error?: boolean
  /** 富结果块（票 24）：text/image；存量条目无此键 */
  blocks?: { type: string; text?: string; media_type?: string; data?: string }[]
}

/** Session 条目（与后端 app/session_store.py 的 JSONL 条目一一对应）。 */
export interface SessionEntry {
  type: string
  id: string
  parentId: string | null
  timestamp: string
  message?: { role: string; content: SessionContentBlock[] }
  usage?: ChatUsage
  stop_reason?: string
  // compaction 条目（ticket 10）
  summary?: string
  first_kept_entry_id?: string | null
  // 分支注记（ticket 17）：仅 message/compaction 条目携带
  branch_index?: number
  branch_total?: number
  siblings?: string[]
}

export interface SessionSummary {
  id: string
  updated_at: number
  title: string
  msg_count: number
  total_tokens: number
  pinned: boolean
  running: boolean
}

/** 重命名 / 置顶（PATCH /api/sessions/{id}，热生效）。 */
export async function patchSession(
  token: string,
  sessionId: string,
  body: { title?: string; pinned?: boolean },
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`patch session failed: ${resp.status}`)
}

export interface ModelInfo {
  id: string
  name: string
  context_window: number
  max_output_tokens: number
  /** 凭证是否就绪（未配置的模型发送会报 API 配置错误） */
  configured?: boolean
}

export interface UsageTotals {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export async function fetchModels(token: string): Promise<ModelInfo[]> {
  const resp = await fetch('/api/models', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch models failed: ${resp.status}`)
  const body = await resp.json()
  return body.models
}

export async function fetchUsage(token: string): Promise<UsageTotals> {
  const resp = await fetch('/api/usage', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch usage failed: ${resp.status}`)
  const body = await resp.json()
  return body.usage
}

export async function fetchSessions(token: string): Promise<SessionSummary[]> {
  const resp = await fetch('/api/sessions', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch sessions failed: ${resp.status}`)
  const body = await resp.json()
  return body.sessions
}

export async function fetchSessionEntries(token: string, sessionId: string): Promise<SessionEntry[]> {
  const resp = await fetch(`/api/sessions/${sessionId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch session failed: ${resp.status}`)
  const body = await resp.json()
  return body.entries
}

/** 删除会话（spec §4.3 唯一的 REST 写端点）。 */
export async function deleteSession(token: string, sessionId: string): Promise<void> {
  const resp = await fetch(`/api/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`delete session failed: ${resp.status}`)
}

/** 任意分支视图（票 17）：根到 entry_id 子树叶的路径，条目带分支注记。 */
export async function fetchSessionBranch(
  token: string,
  sessionId: string,
  entryId: string,
): Promise<SessionEntry[]> {
  const resp = await fetch(`/api/sessions/${sessionId}/branch/${entryId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch branch failed: ${resp.status}`)
  const body = await resp.json()
  return body.entries
}

/** 可用技能（Composer 手动选择菜单，票 18 后续）。 */
export interface SkillInfo {
  name: string
  description: string
}

export async function fetchSkills(token: string): Promise<SkillInfo[]> {
  const resp = await fetch('/api/skills', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch skills failed: ${resp.status}`)
  const body = await resp.json()
  return body.skills
}
