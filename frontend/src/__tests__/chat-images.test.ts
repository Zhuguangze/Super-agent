import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'
import { useAuthStore } from '@/stores/auth'

/** 浏览器 WebSocket 测试替身（与 ws-client.test.ts 同形的客户端语义）。 */
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

  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
}

const PNG_IMAGE = { media_type: 'image/png', data: 'aGk=' }

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  localStorage.clear()
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.token = 'test-token'
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function connectAndOpen(): FakeWebSocket {
  useConnectionStore().ensureConnected('test-token')
  const sock = FakeWebSocket.instances[0]!
  sock.serverOpen()
  return sock
}

// 票 02：带图发送——状态仓缝
describe('票 02：sendPrompt 携带图片', () => {
  it('图文消息:WS payload 含 images,本地消息带图片', () => {
    const chat = useChatStore()
    const sock = connectAndOpen()

    chat.sendPrompt('看这张图', undefined, { images: [PNG_IMAGE] })

    const frame = sock.sent.find((m) => m.cmd === 'prompt')
    expect(frame).toMatchObject({
      text: '看这张图',
      images: [{ media_type: 'image/png', data: 'aGk=' }],
    })
    expect(chat.messages[0]).toMatchObject({ role: 'user', text: '看这张图' })
    expect(chat.messages[0]?.images).toEqual([PNG_IMAGE])
  })

  it('纯图消息(空文本):payload 只带 images,不触发 empty_prompt 拒绝', () => {
    const chat = useChatStore()
    const sock = connectAndOpen()

    chat.sendPrompt('', undefined, { images: [PNG_IMAGE] })

    const frame = sock.sent.find((m) => m.cmd === 'prompt')
    expect(frame).toMatchObject({ text: '', images: [PNG_IMAGE] })
    expect(chat.running).toBe(true)
  })
})
