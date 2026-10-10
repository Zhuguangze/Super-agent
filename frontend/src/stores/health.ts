import { defineStore } from 'pinia'
import { ref } from 'vue'

export type HealthStatus = 'unknown' | 'ok' | 'down'

export const useHealthStore = defineStore('health', () => {
  const status = ref<HealthStatus>('unknown')

  async function check(): Promise<void> {
    try {
      const resp = await fetch('/api/health')
      if (!resp.ok) throw new Error(`bad status: ${resp.status}`)
      const body = (await resp.json()) as { status?: string }
      status.value = body.status === 'ok' ? 'ok' : 'down'
    } catch {
      status.value = 'down'
    }
  }

  return { status, check }
})
