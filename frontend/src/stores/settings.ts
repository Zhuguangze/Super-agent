import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchMcpStatus,
  fetchMcpTools,
  fetchSettings,
  saveMcpServers,
  saveSettings,
  type AppSettings,
  type CustomModelSpec,
  type McpServerSpec,
  type McpStatus,
  type McpToolInfo,
} from '@/api/settings'
import { useAuthStore } from '@/stores/auth'

export const KNOWN_TOOLS = ['read', 'write', 'edit', 'grep', 'find', 'ls', 'fetch', 'bash', 'web_search', 'memory'] as const

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<AppSettings | null>(null)
  const saving = ref(false)
  const lastErrors = ref<string[]>([])
  const lastSaved = ref(false)
  // 已发现的 MCP 工具（票 19）：白名单区动态渲染，与固定 KNOWN_TOOLS 并列
  const mcpTools = ref<McpToolInfo[]>([])
  // MCP 面板状态（票 20）：mcp.json 现内容 + 运行态，编辑即改 config.servers
  const mcpState = ref<McpStatus | null>(null)
  const mcpSaving = ref(false)

  async function load(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    settings.value = await fetchSettings(token)
  }

  /** 拉取已发现的 MCP 工具（票 19）；失败静默为空——白名单区不渲染 MCP 区块。 */
  async function loadMcpTools(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      mcpTools.value = await fetchMcpTools(token)
    } catch {
      mcpTools.value = []
    }
  }

  /** 拉取 MCP 面板状态（票 20）；失败静默为 null——面板显示未配置。 */
  async function loadMcpConfig(): Promise<void> {
    const token = useAuthStore().token
    if (!token) return
    try {
      mcpState.value = await fetchMcpStatus(token)
    } catch {
      mcpState.value = null
    }
  }

  function addMcpServer(name: string, command: string, args: string): void {
    if (!mcpState.value) return
    const trimmed = name.trim()
    if (!trimmed) return
    mcpState.value.config.servers[trimmed] = {
      command: command.trim(),
      args: args.trim() ? args.trim().split(/\s+/) : [],
    }
  }

  function removeMcpServer(name: string): void {
    if (!mcpState.value) return
    delete mcpState.value.config.servers[name]
  }

  /** 保存 MCP 配置（PUT → 写 mcp.json + 重连）；以响应刷新运行态，返回启动错误。 */
  async function saveMcpConfig(): Promise<string[]> {
    const token = useAuthStore().token
    if (!mcpState.value || !token || mcpSaving.value) return []
    mcpSaving.value = true
    try {
      const result = await saveMcpServers(token, mcpState.value.config.servers)
      mcpState.value = {
        ...mcpState.value,
        servers: result.servers,
        tools: result.tools,
        configured: true,
      }
      return result.errors
    } catch (exc) {
      return [String(exc)]
    } finally {
      mcpSaving.value = false
    }
  }

  async function save(): Promise<void> {
    const token = useAuthStore().token
    if (!settings.value || !token || saving.value) return
    saving.value = true
    lastSaved.value = false
    lastErrors.value = []
    try {
      const result = await saveSettings(token, settings.value)
      lastErrors.value = result.custom_model_errors
      lastSaved.value = true
    } finally {
      saving.value = false
    }
  }

  function toggleTool(tool: string): void {
    if (!settings.value) return
    const list = new Set(settings.value.approval_allowlist)
    if (list.has(tool)) list.delete(tool)
    else list.add(tool)
    settings.value.approval_allowlist = [...list].sort()
  }

  function addCustomModel(spec: CustomModelSpec): void {
    if (!settings.value) return
    settings.value.custom_models = [...settings.value.custom_models, spec]
  }

  function removeCustomModel(id: string): void {
    if (!settings.value) return
    settings.value.custom_models = settings.value.custom_models.filter((m) => m.id !== id)
  }

  return {
    settings,
    saving,
    lastErrors,
    lastSaved,
    mcpTools,
    mcpState,
    mcpSaving,
    load,
    loadMcpTools,
    loadMcpConfig,
    addMcpServer,
    removeMcpServer,
    saveMcpConfig,
    save,
    toggleTool,
    addCustomModel,
    removeCustomModel,
  }
})
