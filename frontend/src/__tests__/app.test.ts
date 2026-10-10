import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import App from '../App.vue'
import router from '../router'

// Seam F1：App 挂载——品牌标识 + 主区域路由出口
describe('Seam F1：App 挂载', () => {
  it('渲染品牌标识与主区域路由出口', async () => {
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [createPinia(), router] },
    })

    expect(wrapper.find('.brand').text()).toBe('Super Agent')
    expect(wrapper.find('.app-main').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'RouterView' }).exists()).toBe(true)
  })
})
