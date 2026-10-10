import { defineStore } from 'pinia'
import { ref } from 'vue'
import { WsClient, type WsClientOptions, type WsStatus } from '@/api/ws'

export const useConnectionStore = defineStore('connection', () => {
  const status = ref<WsStatus>('disconnected')

  let client: WsClient | null = null

  function ensureConnected(token: string, opts: WsClientOptions = {}): void {
    if (!client) {
      client = new WsClient(opts)
      client.onStatus = (s) => {
        status.value = s
      }
    }
    client.connect(token)
  }

  function disconnect(): void {
    client?.close()
    client = null
    status.value = 'disconnected'
  }

  /** 供 ticket 03 的流式对话消费：命令发送与事件订阅。 */
  function getClient(): WsClient | null {
    return client
  }

  /** 注册下行事件处理器（事件流由 chat store 消费）。 */
  function setEventHandler(handler: (data: Record<string, unknown>) => void): void {
    if (client) client.onEvent = handler
  }

  return { status, ensureConnected, disconnect, getClient, setEventHandler }
})
