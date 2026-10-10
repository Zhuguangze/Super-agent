import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { WsClient } from '../api/ws'
import { useConnectionStore } from '../stores/connection'
import ConnectionBadge from '../components/ConnectionBadge.vue'
import App from '../App.vue'
import router from '../router'

/** 浏览器 WebSocket 的测试替身：只暴露客户端语义（url、send、四类事件）。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3

  url: string
  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((ev: { code: number }) => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  sent: Record<string, unknown>[] = []

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data))
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
  }

  // —— 测试辅助：模拟服务端行为 ——
  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  serverMessage(obj: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }

  serverClose(code = 1006): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code })
  }
}

// Seam F4：WS 客户端——带 token 连接、req_id 关联、断线重连、握手失败按未认证处理
describe('Seam F4：WS 客户端', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('连接 URL 携带 token', () => {
    const client = new WsClient({ url: 'ws://test/ws' })
    client.connect('my-token')

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0]!.url).toBe('ws://test/ws?token=my-token')
    expect(client.status).toBe('connecting')
  })

  it('send 按 req_id 关联响应', async () => {
    const client = new WsClient({ url: 'ws://test/ws' })
    client.connect('t')
    const sock = FakeWebSocket.instances[0]!
    sock.serverOpen()

    const pending = client.send('echo', { text: 'hi' })
    expect(sock.sent[0]).toMatchObject({ cmd: 'echo', req_id: '1', text: 'hi' })

    sock.serverMessage({ type: 'response', req_id: '1', text: 'hi' })
    await expect(pending).resolves.toMatchObject({ text: 'hi' })
  })

  it('连接建立后断线 → 重连中，并自动重连', async () => {
    const client = new WsClient({ url: 'ws://test/ws', delayMs: 0 })
    client.connect('t')
    const first = FakeWebSocket.instances[0]!
    first.serverOpen()
    first.serverClose(1006)

    expect(client.status).toBe('reconnecting')
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2))
    expect(FakeWebSocket.instances[1]!.url).toContain('token=t')
  })

  it('握手失败（未 open 即 close）→ unauthorized，且不再重连', async () => {
    const client = new WsClient({ url: 'ws://test/ws', delayMs: 0 })
    client.connect('bad-token')
    const sock = FakeWebSocket.instances[0]!
    sock.serverClose(1006)

    await new Promise((r) => setTimeout(r, 20))
    expect(client.status).toBe('unauthorized')
    expect(FakeWebSocket.instances).toHaveLength(1) // 未发起第二次连接
  })

  it('登录后 App 自动建立连接（装配胶水）', async () => {
    localStorage.setItem('super_agent_token', 'glue-token')
    await router.push('/')
    await router.isReady()
    mount(App, {
      global: { plugins: [createPinia(), router] },
      attachTo: document.body,
    })
    await flushPromises()

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
    expect(FakeWebSocket.instances[0]!.url).toContain('token=glue-token')
  })

  it('ConnectionBadge 按连接状态渲染提示', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const connection = useConnectionStore()

    connection.status = 'reconnecting'
    expect(mount(ConnectionBadge, { global: { plugins: [pinia] } }).text()).toContain('重连中')

    connection.status = 'unauthorized'
    expect(mount(ConnectionBadge, { global: { plugins: [pinia] } }).text()).toContain(
      '请重新登录',
    )
  })
})
