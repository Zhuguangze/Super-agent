import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import App from '../App.vue'
import router from '../router'

async function mountApp() {
  await router.push('/')
  await router.isReady()
  return mount(App, {
    global: { plugins: [createPinia(), router] },
  })
}

// Seam F2：健康徽标——store 经网络层调 /api/health，组件渲染状态徽标
describe('Seam F2：健康徽标', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('后端返回 ok 时显示在线', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
      ),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.find('.health-badge').text()).toContain('在线')
  })

  it('网络失败时显示离线', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down')
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.find('.health-badge').text()).toContain('离线')
  })
})
