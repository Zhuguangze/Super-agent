import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/auth'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

const SETTINGS = {
  approval_allowlist: ['read'],
  default_model: 'claude-opus-5',
  max_concurrency: 4,
  custom_models: [] as { id: string; name: string; provider: string; base_url: string; api_key: string }[],
}

function stubFetch(handler: (url: string, init?: { method?: string; body?: string }) => unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: { method?: string; body?: string }) => ({
      ok: true,
      json: async () => handler(url, init),
    })),
  )
}

describe('settings store（Settings 视图）', () => {
  it('load 拉取设置', async () => {
    stubFetch(() => SETTINGS)
    useAuthStore().token = 'tok'
    await useSettingsStore().load()
    expect(useSettingsStore().settings?.max_concurrency).toBe(4)
  })

  it('toggleTool 勾选/取消工具', async () => {
    stubFetch(() => SETTINGS)
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.load()
    s.toggleTool('bash')
    expect(s.settings?.approval_allowlist).toContain('bash')
    s.toggleTool('bash')
    expect(s.settings?.approval_allowlist).not.toContain('bash')
  })

  it('addCustomModel / removeCustomModel', async () => {
    stubFetch(() => SETTINGS)
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.load()
    s.addCustomModel({ id: 'local-qwen', name: '', provider: 'openai-compat', base_url: 'http://x/v1', api_key: '' })
    expect(s.settings?.custom_models).toHaveLength(1)
    s.removeCustomModel('local-qwen')
    expect(s.settings?.custom_models).toHaveLength(0)
  })

  it('save 提交 PUT 并记录 custom_model_errors', async () => {
    const bodies: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: { method?: string; body?: string }) => {
        if (init?.method === 'PUT') {
          bodies.push(init.body ?? '')
          return { ok: true, json: async () => ({ ...SETTINGS, custom_model_errors: ['模型 ID 冲突'] }) }
        }
        return { ok: true, json: async () => SETTINGS }
      }),
    )
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.load()
    s.settings!.max_concurrency = 2
    await s.save()
    expect(JSON.parse(bodies[0]!).max_concurrency).toBe(2)
    expect(s.lastErrors).toEqual(['模型 ID 冲突'])
    expect(s.lastSaved).toBe(true)
  })
})

describe('MCP 动态工具白名单（票 19）', () => {
  const MCP_TOOLS = [
    { name: 'mcp__fake__echo', server: 'fake', description: '回声' },
    { name: 'mcp__fake__fail', server: 'fake', description: '失败' },
  ]

  it('loadMcpTools 拉取已发现的 MCP 工具', async () => {
    stubFetch((url) => (String(url) === '/api/mcp/tools' ? { tools: MCP_TOOLS } : SETTINGS))
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpTools()
    expect(s.mcpTools).toHaveLength(2)
    expect(s.mcpTools[0]).toMatchObject({ name: 'mcp__fake__echo', server: 'fake' })
  })

  it('拉取失败静默为空列表（不打断 Settings 页）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpTools()
    expect(s.mcpTools).toEqual([])
  })

  it('toggleTool 勾选 MCP 动态工具（任意名字可用的契约锁）', async () => {
    stubFetch(() => SETTINGS)
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.load()
    s.toggleTool('mcp__fake__echo')
    expect(s.settings?.approval_allowlist).toContain('mcp__fake__echo')
  })

  it('SettingsView 白名单区渲染 MCP 工具并带 server 徽标', async () => {
    const { mount, flushPromises } = await import('@vue/test-utils')
    const { default: SettingsView } = await import('@/views/SettingsView.vue')
    const VIEW_SETTINGS = { ...SETTINGS, approval_enabled: true, bash_timeout: 60 }
    stubFetch((url) => {
      const u = String(url)
      if (u === '/api/mcp/tools') return { tools: MCP_TOOLS }
      if (u === '/api/models') return { models: [] }
      return VIEW_SETTINGS
    })
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.load()
    await s.loadMcpTools()
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()

    const rows = wrapper.findAll('.mcp-tool-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('mcp__fake__echo')
    expect(rows[0]!.text()).toContain('fake') // server 来源徽标
    await rows[0]!.find('input[type="checkbox"]').setValue(true)
    expect(s.settings?.approval_allowlist).toContain('mcp__fake__echo')
  })
})

describe('MCP 管理面板（票 20）', () => {
  const MCP_STATUS_TEMPLATE = {
    configured: true,
    config: { servers: { fake: { command: 'python', args: ['x'] } } },
    servers: [{ name: 'fake', status: 'running', error: '' }],
    tools: [{ name: 'mcp__fake__echo', server: 'fake', description: '回声' }],
  }
  // store 会就地编辑 config.servers——每个用例给深拷贝，防止 fixture 跨用例污染
  const mcpStatus = () => JSON.parse(JSON.stringify(MCP_STATUS_TEMPLATE))

  it('loadMcpConfig 拉取状态；失败静默为 null', async () => {
    stubFetch((url) => (String(url) === '/api/mcp' ? mcpStatus() : SETTINGS))
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpConfig()
    expect(s.mcpState?.configured).toBe(true)
    expect(Object.keys(s.mcpState?.config.servers ?? {})).toEqual(['fake'])

    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))
    await s.loadMcpConfig()
    expect(s.mcpState).toBeNull()
  })

  it('addMcpServer / removeMcpServer 修改待保存配置', async () => {
    stubFetch((url) => (String(url) === '/api/mcp' ? mcpStatus() : SETTINGS))
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpConfig()
    s.addMcpServer('tools2', 'python', 'a b')
    expect(s.mcpState?.config.servers.tools2).toEqual({ command: 'python', args: ['a', 'b'] })
    s.removeMcpServer('fake')
    expect(s.mcpState?.config.servers.fake).toBeUndefined()
  })

  it('saveMcpConfig PUT 并以响应刷新运行态', async () => {
    stubFetch((url, init) => {
      if (String(url) === '/api/mcp' && init?.method === 'PUT') {
        return { ok: true, errors: [], servers: [{ name: 'tools2', status: 'running', error: '' }], tools: [] }
      }
      if (String(url) === '/api/mcp') return mcpStatus()
      return SETTINGS
    })
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpConfig()
    s.addMcpServer('tools2', 'py', '')
    const errors = await s.saveMcpConfig()
    expect(errors).toEqual([])
    expect(s.mcpState?.servers[0]?.name).toBe('tools2')
  })

  it('SettingsView MCP 区渲染 server 行、状态徽标与工具，可删除', async () => {
    const { mount, flushPromises } = await import('@vue/test-utils')
    const { default: SettingsView } = await import('@/views/SettingsView.vue')
    const VIEW_SETTINGS = { ...SETTINGS, approval_enabled: true, bash_timeout: 60 }
    stubFetch((url) => {
      const u = String(url)
      if (u === '/api/mcp') return mcpStatus()
      if (u === '/api/mcp/tools') return { tools: [] }
      if (u === '/api/models') return { models: [] }
      return VIEW_SETTINGS
    })
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpConfig()
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()

    const rows = wrapper.findAll('.mcp-server-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('fake')
    expect(rows[0]!.text()).toContain('python x')
    expect(rows[0]!.find('.st-badge.running').exists()).toBe(true)
    // 发现的工具以徽标展示
    expect(wrapper.find('.mcp-tools').text()).toContain('mcp__fake__echo')
    // 删除
    await rows[0]!.find('button.mcpx').trigger('click')
    expect(s.mcpState?.config.servers.fake).toBeUndefined()
  })

  it('SettingsView MCP 添加表单：提交后 PUT 载荷为当前 servers', async () => {
    const { mount, flushPromises } = await import('@vue/test-utils')
    const { default: SettingsView } = await import('@/views/SettingsView.vue')
    const VIEW_SETTINGS = { ...SETTINGS, approval_enabled: true, bash_timeout: 60 }
    const puts: string[] = []
    stubFetch((url, init) => {
      const u = String(url)
      if (u === '/api/mcp' && init?.method === 'PUT') {
        puts.push(init.body ?? '')
        return { ok: true, errors: [], servers: [{ name: 'fs', status: 'running', error: '' }], tools: [] }
      }
      if (u === '/api/mcp') return mcpStatus()
      if (u === '/api/mcp/tools') return { tools: [] }
      if (u === '/api/models') return { models: [] }
      return VIEW_SETTINGS
    })
    useAuthStore().token = 'tok'
    const s = useSettingsStore()
    await s.loadMcpConfig()
    const wrapper = mount(SettingsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()

    const form = wrapper.find('form.mcp-add')
    await form.find('input[placeholder*="server 名"]').setValue('fs')
    await form.find('input[placeholder*="command"]').setValue('npx')
    await form.find('input[placeholder*="args"]').setValue('-y @x/server')
    await form.trigger('submit')
    await flushPromises()
    await wrapper.find('button.mcpsave').trigger('click')
    await flushPromises()

    expect(puts).toHaveLength(1)
    const payload = JSON.parse(puts[0]!)
    expect(payload.servers.fs).toEqual({ command: 'npx', args: ['-y', '@x/server'] })
    expect(payload.servers.fake).toEqual({ command: 'python', args: ['x'] }) // 未触字段原样保留
    expect(s.mcpState?.servers[0]?.name).toBe('fs') // 运行态随响应刷新
  })
})
