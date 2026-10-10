import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import App from '../App.vue'
import router from '../router'

function stubFetch(
  handler: (url: string, init?: RequestInit) => Response | Promise<Response>,
) {
  vi.stubGlobal('fetch', vi.fn(async (url: string | URL, init?: RequestInit) => handler(String(url), init)))
}

function credentialsHandler(ok: boolean) {
  return (url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}'))
    if (url === '/api/auth/login') {
      if (!ok) return new Response('{"detail":"invalid credentials"}', { status: 401 })
      return new Response(
        JSON.stringify({ token: 'test-token-1', username: body.username }),
        { status: 200 },
      )
    }
    if (url === '/api/auth/register') {
      if (!ok) return new Response('{"detail":"username already taken"}', { status: 409 })
      return new Response(JSON.stringify({ username: body.username }), { status: 201 })
    }
    return new Response('{}', { status: 200 })
  }
}

async function mountAt(path: string) {
  await router.push(path)
  await router.isReady()
  const wrapper = mount(App, {
    global: { plugins: [createPinia(), router] },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

// Seam F3：登录——表单提交 → 跳转主界面；未登录访问被守卫重定向
describe('Seam F3：登录', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('登录成功：token 落 localStorage 并跳转主界面', async () => {
    stubFetch(credentialsHandler(true))
    const wrapper = await mountAt('/login')

    await wrapper.find('input[type="text"]').setValue('zhuguangze')
    await wrapper.find('input[type="password"]').setValue('secret123')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/')
    expect(localStorage.getItem('super_agent_token')).toBe('test-token-1')
  })

  it('登录失败：显示错误并停留在登录页', async () => {
    stubFetch(credentialsHandler(false))
    const wrapper = await mountAt('/login')

    await wrapper.find('input[type="text"]').setValue('zhuguangze')
    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/login')
    expect(wrapper.find('.login-error').text()).not.toBe('')
  })

  it('未登录访问主界面被重定向到登录页', async () => {
    const wrapper = await mountAt('/')
    expect(router.currentRoute.value.path).toBe('/login')
    wrapper.unmount()
  })

  it('注册模式：注册成功后自动登录并跳转', async () => {
    stubFetch(credentialsHandler(true))
    const wrapper = await mountAt('/login')

    await wrapper.find('.login-switch').trigger('click')
    await wrapper.find('input[type="text"]').setValue('newuser')
    await wrapper.find('input[type="password"]').setValue('secret123')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/')
    expect(localStorage.getItem('super_agent_token')).toBe('test-token-1')
  })
})
