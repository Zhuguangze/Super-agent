import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

const TOKEN_KEY = 'super_agent_token'
const USERNAME_KEY = 'super_agent_username'

/** 路由守卫用：不依赖 active Pinia（守卫可能在应用安装 pinia 之前触发）。 */
export function hasStoredToken(): boolean {
  return localStorage.getItem(TOKEN_KEY) !== null
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const username = ref<string | null>(localStorage.getItem(USERNAME_KEY))

  const isAuthenticated = computed(() => token.value !== null)

  async function login(name: string, password: string): Promise<void> {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: name, password }),
    })
    if (!resp.ok) throw new Error(`login failed: ${resp.status}`)
    await applySession(await resp.json())
  }

  async function register(name: string, password: string): Promise<void> {
    const resp = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: name, password }),
    })
    if (!resp.ok) throw new Error(`register failed: ${resp.status}`)
  }

  function logout(): void {
    token.value = null
    username.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USERNAME_KEY)
  }

  async function applySession(body: { token: string; username: string }): Promise<void> {
    token.value = body.token
    username.value = body.username
    localStorage.setItem(TOKEN_KEY, body.token)
    localStorage.setItem(USERNAME_KEY, body.username)
  }

  return { token, username, isAuthenticated, login, register, logout }
})
