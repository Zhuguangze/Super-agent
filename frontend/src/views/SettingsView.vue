<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchModels, type ModelInfo } from '@/api/rest'
import type { CustomModelSpec } from '@/api/settings'
import { useAuthStore } from '@/stores/auth'
import { KNOWN_TOOLS, useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()
const models = ref<ModelInfo[]>([])
const loadError = ref('')
const toastMsg = ref('')

const newModel = ref({
  id: '',
  name: '',
  provider: 'openai-compat' as CustomModelSpec['provider'],
  base_url: '',
  api_key: '',
  context_window: 128000,
})
// api_key 留空的语义随 provider 而异：anthropic 回退服务端 ANTHROPIC_API_KEY，
// openai-compat 则完全不发 Authorization 头（本地 Ollama/vLLM 常无需鉴权）。
const apiKeyPlaceholder = computed(() =>
  newModel.value.provider === 'anthropic'
    ? 'API Key（留空则用服务端 ANTHROPIC_API_KEY 环境变量）'
    : 'API Key（留空则不发送 Authorization，适用于本地端点）',
)

// 自定义模型行内编辑：editingId 为 null 时全部收起
const editingId = ref<string | null>(null)
const editDraft = ref<CustomModelSpec>({
  id: '',
  name: '',
  provider: 'openai-compat',
  base_url: '',
  api_key: '',
  context_window: 128000,
})

// MCP 面板（票 20）：添加表单 + 保存重连
const newSrv = ref({ name: '', command: '', args: '' })
const mcpErrors = ref<string[]>([])

function mcpStatusOf(name: string): string {
  return settings.mcpState?.servers.find((r) => r.name === name)?.status ?? 'unknown'
}

function addServer(): void {
  settings.addMcpServer(newSrv.value.name, newSrv.value.command, newSrv.value.args)
  newSrv.value = { name: '', command: '', args: '' }
}

async function saveMcp(): Promise<void> {
  mcpErrors.value = await settings.saveMcpConfig()
}

onMounted(async () => {
  try {
    await settings.load()
    models.value = await fetchModels(useAuthStore().token!)
  } catch {
    loadError.value = '设置加载失败（请确认服务可用）'
  }
  void settings.loadMcpTools() // MCP 工具发现失败静默（票 19）
  void settings.loadMcpConfig() // 面板状态（票 20）
})

const builtinModels = computed(() => models.value.filter((m) => !isCustom(m.id)))
const customRows = computed(() => settings.settings?.custom_models ?? [])

function isCustom(id: string): boolean {
  return !!settings.settings?.custom_models.some((m) => m.id === id)
}

function isDefault(id: string): boolean {
  return settings.settings?.default_model === id
}

function setDefault(id: string): void {
  if (settings.settings) settings.settings.default_model = id
}

function addModel(): void {
  const spec = newModel.value
  if (!spec.id.trim() || !spec.base_url.trim()) return
  settings.addCustomModel({
    ...spec,
    id: spec.id.trim(),
    base_url: spec.base_url.trim(),
    context_window: spec.context_window || 128000,
  })
  newModel.value = {
    id: '',
    name: '',
    provider: 'openai-compat',
    base_url: '',
    api_key: '',
    context_window: 128000,
  }
}

function startEdit(m: CustomModelSpec): void {
  editingId.value = m.id
  editDraft.value = { ...m } // 副本：取消时天然丢弃
}

function cancelEdit(): void {
  editingId.value = null
}

function saveEdit(): void {
  if (!settings.settings || !editingId.value) return
  const next = editDraft.value
  if (!next.base_url.trim()) return
  settings.settings.custom_models = settings.settings.custom_models.map((m) =>
    m.id === editingId.value
      ? { ...next, base_url: next.base_url.trim(), context_window: next.context_window || 128000 }
      : m,
  )
  editingId.value = null
}

function removeModel(id: string): void {
  if (editingId.value === id) editingId.value = null
  if (isDefault(id) && models.value.length > 1) {
    const next = models.value.find((m) => m.id !== id)
    if (next && settings.settings) settings.settings.default_model = next.id
  }
  settings.removeCustomModel(id)
}

async function save(): Promise<void> {
  await settings.save()
  toastMsg.value = settings.lastErrors.length
    ? '已保存（自定义模型有警告）'
    : '已保存 — 立即生效'
  setTimeout(() => (toastMsg.value = ''), 2500)
}
</script>

<template>
  <div class="se">
    <div class="se-top">
      <span class="se-title">Settings</span>
      <span class="chip">settings.json · 保存即热生效</span>
      <RouterLink class="btn btn-ghost" to="/">← 返回工作台</RouterLink>
    </div>
    <div class="se-body">
      <div class="se-col">
        <p v-if="loadError" class="se-error">{{ loadError }}</p>

        <template v-if="settings.settings">
          <div class="se-sec">
            <h3>模型</h3>
            <p class="desc">默认模型与 thinking 强度；自定义模型并入 GET /api/models，OpenAI 兼容端点（vLLM / Ollama / DeepSeek…）与自定义 base_url 均可接入。</p>
            <div class="se-label">可用模型 — 点击行设为默认；自定义行可编辑或移除</div>
            <div class="se-rows">
              <button
                v-for="m in builtinModels"
                :key="m.id"
                class="se-row se-mrow"
                :class="{ def: isDefault(m.id) }"
                type="button"
                @click="setDefault(m.id)"
              >
                <span class="pat">{{ m.id }}</span>
                <span v-if="isDefault(m.id)" class="se-badge def">默认</span>
                <span class="sp"></span>
                <span class="se-badge">Anthropic</span>
              </button>
              <template v-for="m in customRows" :key="m.id">
                <div
                  v-if="editingId !== m.id"
                  class="se-row se-mrow"
                  :class="{ def: isDefault(m.id) }"
                  role="button"
                  tabindex="0"
                  @click="setDefault(m.id)"
                >
                  <span class="pat">{{ m.id }}</span>
                  <span v-if="isDefault(m.id)" class="se-badge def">默认</span>
                  <span class="sp"></span>
                  <span class="se-badge" :title="`上下文 ${m.context_window ?? 128000} tokens`">
                    {{ Math.round((m.context_window ?? 128000) / 1000) }}K ctx
                  </span>
                  <span class="se-badge" :title="m.base_url">{{ m.provider === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容' }}</span>
                  <button class="se-x sedit" title="编辑" @click.stop="startEdit(m)">✎</button>
                  <button class="se-x" title="移除" @click.stop="removeModel(m.id)">✕</button>
                </div>
                <form v-else class="se-row se-edit" @submit.prevent="saveEdit">
                  <span class="pat">{{ m.id }}</span>
                  <select v-model="editDraft.provider">
                    <option value="openai-compat">OpenAI 兼容</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                  <input v-model="editDraft.base_url" placeholder="Base URL" />
                  <input
                    v-model="editDraft.api_key"
                    type="password"
                    autocomplete="new-password"
                    spellcheck="false"
                    :placeholder="apiKeyPlaceholder"
                  />
                  <input v-model.number="editDraft.context_window" type="number" min="1000" placeholder="上下文窗口" />
                  <button class="btn" type="submit">保存</button>
                  <button class="se-x" type="button" title="取消" @click="cancelEdit">✕</button>
                </form>
              </template>
            </div>
            <form class="se-add" @submit.prevent="addModel">
              <input v-model="newModel.id" placeholder="模型 ID，如 local-qwen" required />
              <select v-model="newModel.provider">
                <option value="openai-compat">OpenAI 兼容</option>
                <option value="anthropic">Anthropic</option>
              </select>
              <button class="btn" type="submit">添加</button>
              <input v-model="newModel.base_url" placeholder="Base URL，如 http://127.0.0.1:11434/v1" />
              <input
                v-model="newModel.api_key"
                type="password"
                autocomplete="new-password"
                spellcheck="false"
                :placeholder="apiKeyPlaceholder"
              />
              <input v-model.number="newModel.context_window" type="number" min="1000" placeholder="上下文窗口（tokens，如 128000）" />
            </form>
            <p class="hint">思考强度按会话在工作台输入框的「✦ 思考」菜单切换（set_thinking_level，随下一条消息生效）。</p>
          </div>

          <div class="se-sec">
            <h3>Tool Approval</h3>
            <p class="desc">非白名单工具（bash 等）执行前推送 approval_request，确认后放行；关闭开关即回到 pi 的无审批行为。</p>
            <div class="se-togrow">
              <div>
                <div class="tt">执行前需要审批</div>
                <div class="dd">关闭后所有工具直接执行（内网信任环境可自行权衡）</div>
              </div>
              <span class="sp"></span>
              <button
                class="se-switch"
                :class="{ on: settings.settings.approval_enabled }"
                type="button"
                aria-label="切换审批开关"
                @click="settings.settings.approval_enabled = !settings.settings.approval_enabled"
              ></button>
            </div>
            <template v-if="settings.settings.approval_enabled">
              <div class="se-label">白名单工具 — 勾选后不经审批直接执行</div>
              <div class="se-rows">
                <label v-for="tool in KNOWN_TOOLS" :key="tool" class="se-row se-mrow">
                  <span class="pat">{{ tool }}</span>
                  <span class="sp"></span>
                  <input
                    type="checkbox"
                    :checked="settings.settings.approval_allowlist.includes(tool)"
                    @change="settings.toggleTool(tool)"
                  />
                </label>
                <!-- MCP 动态工具（票 19）：server 徽标标注来源，勾选同样免审批 -->
                <label
                  v-for="tool in settings.mcpTools"
                  :key="tool.name"
                  class="se-row se-mrow mcp-tool-row"
                >
                  <span class="pat">{{ tool.name }}</span>
                  <span class="sp"></span>
                  <span class="se-badge" :title="tool.description">{{ tool.server }}</span>
                  <input
                    type="checkbox"
                    :checked="settings.settings.approval_allowlist.includes(tool.name)"
                    @change="settings.toggleTool(tool.name)"
                  />
                </label>
              </div>
            </template>
          </div>

          <div class="se-sec">
            <h3>MCP 服务器</h3>
            <p class="desc">mcp.json 的界面化管理：增删改后「保存并重连」即热生效（运行中的旧调用以错误结果回填，会话不中断）。</p>
            <template v-if="settings.mcpState">
              <p v-if="!settings.mcpState.configured" class="hint">
                尚未配置任何 MCP 服务器——添加第一个并保存即启用。
              </p>
              <div v-if="settings.mcpState.configured" class="se-rows">
                <div
                  v-for="(spec, name) in settings.mcpState.config.servers"
                  :key="name"
                  class="se-row se-mrow mcp-server-row"
                >
                  <span class="pat">{{ name }}</span>
                  <span class="mcp-cmd" :title="`${spec.command} ${(spec.args ?? []).join(' ')}`">
                    {{ spec.command }} {{ (spec.args ?? []).join(' ') }}
                  </span>
                  <span class="sp"></span>
                  <span class="se-badge st-badge" :class="mcpStatusOf(String(name))">
                    {{ mcpStatusOf(String(name)) === 'running' ? '运行中' : mcpStatusOf(String(name)) === 'failed' ? '失败' : '—' }}
                  </span>
                  <button class="se-x mcpx" title="移除" @click.stop="settings.removeMcpServer(String(name))">✕</button>
                </div>
              </div>
              <template v-if="settings.mcpState.configured && settings.mcpState.tools.length">
                <div class="se-label">发现的工具</div>
                <div class="mcp-tools">
                  <span
                    v-for="t in settings.mcpState.tools"
                    :key="t.name"
                    class="se-badge"
                    :title="t.description"
                  >{{ t.name }}</span>
                </div>
              </template>
              <form class="se-add mcp-add" @submit.prevent="addServer">
                <input v-model="newSrv.name" placeholder="server 名，如 filesystem" required />
                <input v-model="newSrv.command" class="wide" placeholder="command，如 npx" required />
                <input v-model="newSrv.args" class="wide" placeholder="args（空格分隔，可空）" />
                <button class="btn" type="submit">添加</button>
              </form>
              <div class="se-savebar">
                <button
                  class="btn btn-primary mcpsave"
                  :disabled="settings.mcpSaving || !settings.mcpState.configured"
                  @click="saveMcp"
                >
                  {{ settings.mcpSaving ? '保存中…' : '保存并重连' }}
                </button>
                <span v-if="mcpErrors.length" class="hint mcp-err">{{ mcpErrors.join('；') }}</span>
                <span v-else-if="settings.mcpState.configured" class="hint">保存后立即重连，工具清单随新配置刷新</span>
              </div>
            </template>
          </div>

          <div class="se-sec">
            <h3>运行</h3>
            <p class="desc">每个运行中的 Agent Loop 是一个 asyncio task；全局上限超出即排队，bash 子进程强制超时。</p>
            <div class="se-numrow">
              <div class="se-numf">
                <label>全局并发 Loop 上限</label>
                <input v-model.number="settings.settings.max_concurrency" type="number" min="1" max="32" />
              </div>
              <div class="se-numf">
                <label>bash 超时（秒）</label>
                <input v-model.number="settings.settings.bash_timeout" type="number" min="1" max="300" />
              </div>
            </div>
          </div>

          <div class="se-savebar">
            <button class="btn btn-primary" :disabled="settings.saving" @click="save">
              {{ settings.saving ? '保存中…' : '保存' }}
            </button>
            <span v-if="settings.lastSaved" class="hint ok">已保存并即时生效</span>
            <span v-else class="hint">保存在 settings.json，热生效无需重启</span>
          </div>
          <ul v-if="settings.lastErrors.length" class="se-errors">
            <li v-for="e in settings.lastErrors" :key="e">{{ e }}</li>
          </ul>
        </template>
      </div>
    </div>
    <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
  </div>
</template>

<style scoped>
.se{height:100%;display:flex;flex-direction:column}
.se-top{flex:none;display:flex;align-items:center;gap:12px;padding:16px 26px;border-bottom:1px solid var(--line2)}
.se-title{font-size:16px;font-weight:600;margin-right:auto}
.se-body{flex:1;overflow-y:auto;min-height:0}
.se-col{max-width:680px;margin:0 auto;padding:28px 26px 90px;display:flex;flex-direction:column;gap:30px}
.se-sec h3{margin:0 0 2px;font-size:14.5px;font-weight:600}
.se-sec .desc{margin:0 0 14px;font-size:12.5px;color:var(--ink2);line-height:1.65}
.se-label{font-size:12px;font-weight:500;color:var(--ink2);margin:14px 0 7px}
.se-rows{border:1px solid var(--line2);border-radius:10px;overflow:hidden;background:var(--surface)}
.se-row{display:flex;align-items:center;gap:12px;padding:9px 14px;border-bottom:1px solid var(--line2)}
.se-row:last-child{border-bottom:none}
.se-row .pat{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:12.5px}
.se-row .sp{flex:1}
.se-row input[type="checkbox"]{accent-color:var(--accent)}
.se-badge{font-size:10.5px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--ink3);white-space:nowrap}
.se-badge.def{color:var(--accent);border-color:var(--accent);font-weight:500}
.se-mrow{cursor:pointer}
.se-mrow:hover{background:var(--surface2)}
.se-mrow.def{background:var(--accent-soft)}
.se-x{width:24px;height:24px;border-radius:6px;display:grid;place-items:center;color:var(--ink3);font-size:13px}
.se-x:hover{background:var(--error-soft);color:var(--error)}
/* 自定义模型行内编辑 */
.se-edit{flex-wrap:wrap}
.se-edit input,.se-edit select{border:1px solid var(--line);border-radius:10px;background:var(--surface2);padding:8px 12px;font-size:13px;outline:none;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.se-edit input:focus,.se-edit select:focus{border-color:var(--accent)}
.se-edit input{flex:1;min-width:150px}
.se-x.sedit:hover{background:var(--surface2);color:var(--accent)}
.se-add{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.se-add input{flex:1;min-width:140px;border:1px solid var(--line);border-radius:10px;background:var(--surface2);padding:8px 12px;font-size:13px;outline:none;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.se-add input:focus{border-color:var(--accent)}
.se-add select{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:8px 10px;font-size:13px;color:var(--ink2);outline:none}
.se-togrow{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--line2);border-radius:10px;background:var(--surface)}
.se-togrow .tt{font-size:13.5px;font-weight:500}
.se-togrow .dd{font-size:12px;color:var(--ink2);margin-top:1px}
.se-togrow .sp{flex:1}
.se-switch{position:relative;width:40px;height:22px;border-radius:999px;background:var(--line);transition:background .15s;flex:none;border:none;cursor:pointer}
.se-switch::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:var(--surface);box-shadow:0 1px 3px rgba(16,20,26,.3);transition:left .15s}
.se-switch.on{background:var(--accent)}
.se-switch.on::after{left:20px}
.se-numrow{display:flex;gap:22px;flex-wrap:wrap}
.se-numf label{display:block;font-size:12px;font-weight:500;color:var(--ink2);margin-bottom:6px}
.se-numf input{width:110px;border:1px solid var(--line);border-radius:10px;background:var(--surface2);padding:8px 12px;font-size:13px;outline:none;font-variant-numeric:tabular-nums}
.se-numf input:focus{border-color:var(--accent)}
.se-savebar{display:flex;align-items:center;gap:12px}
.se-savebar .hint{font-size:11.5px;color:var(--ink3)}
.se-savebar .ok{color:#2e7d32}
.se-errors{margin:0;padding:0;list-style:none;font-size:12px;color:var(--error)}
/* MCP 面板（票 20） */
.mcp-cmd{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink2);font-size:12px;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.st-badge.running{color:var(--accent);border-color:var(--accent)}
.st-badge.failed{color:var(--error);border-color:var(--error)}
.mcp-tools{display:flex;flex-wrap:wrap;gap:6px}
.mcp-err{color:var(--error)}
.se-hint,.hint{font-size:12px;color:var(--ink3)}
.se-error{margin:0;padding:10px 14px;border:1px solid var(--error);border-radius:10px;background:var(--error-soft);color:var(--error);font-size:13px}
</style>
