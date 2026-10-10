/** Settings API（spec §4.3）——审批白名单、默认模型、并发上限、自定义模型。 */

import type { UsageTotals } from '@/api/rest'

export interface CustomModelSpec {
  id: string
  name: string
  provider: 'openai-compat' | 'anthropic'
  base_url: string
  api_key: string
  /** 上下文窗口（token 数）；缺省 128000 */
  context_window?: number
}

export interface AppSettings {
  approval_allowlist: string[]
  approval_enabled: boolean
  default_model: string
  max_concurrency: number
  bash_timeout: number
  custom_models: CustomModelSpec[]
}

export async function fetchSettings(token: string): Promise<AppSettings> {
  const resp = await fetch('/api/settings', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch settings failed: ${resp.status}`)
  return resp.json()
}

export async function saveSettings(token: string, settings: AppSettings): Promise<AppSettings & { custom_model_errors: string[] }> {
  const resp = await fetch('/api/settings', {
    method: 'PUT',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null)
    throw new Error(detail?.detail ?? `save settings failed: ${resp.status}`)
  }
  return resp.json()
}

export type { UsageTotals }

/** 已发现的 MCP 工具（票 19：白名单勾选数据源）。 */
export interface McpToolInfo {
  name: string
  server: string
  description: string
}

export interface McpServerSpec {
  command: string
  args?: string[]
  env?: Record<string, string>
}

export interface McpServerRow {
  name: string
  status: 'running' | 'failed'
  error: string
}

/** MCP 面板状态（票 20）：mcp.json 现内容 + 运行态 + 已发现工具。 */
export interface McpStatus {
  configured: boolean
  config: { servers: Record<string, McpServerSpec> }
  servers: McpServerRow[]
  tools: McpToolInfo[]
}

export async function fetchMcpTools(token: string): Promise<McpToolInfo[]> {
  const resp = await fetch('/api/mcp/tools', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch mcp tools failed: ${resp.status}`)
  const body = await resp.json()
  return body.tools
}

export async function fetchMcpStatus(token: string): Promise<McpStatus> {
  const resp = await fetch('/api/mcp', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error(`fetch mcp status failed: ${resp.status}`)
  return resp.json()
}

export async function saveMcpServers(
  token: string,
  servers: Record<string, McpServerSpec>,
): Promise<{ ok: true; errors: string[]; servers: McpServerRow[]; tools: McpToolInfo[] }> {
  const resp = await fetch('/api/mcp', {
    method: 'PUT',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ servers }),
  })
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null)
    throw new Error(detail?.detail ?? `save mcp failed: ${resp.status}`)
  }
  return resp.json()
}
