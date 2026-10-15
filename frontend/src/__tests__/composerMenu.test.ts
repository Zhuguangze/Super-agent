import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import Composer from '../components/Composer.vue'
import { useChatStore } from '../stores/chat'

// 真实浏览器时序回归:v-click-outside 在 pointerdown 捕获阶段触发,
// 若误关了"别的 wrapper 正打开的菜单",菜单 DOM 会在 click 派发前被拆掉,
// 菜单项的 pick 回调永远收不到点击（历史 bug:思考/技能无法选择）。
describe('Composer 弹出菜单 × v-click-outside', () => {
  let pinia: Pinia

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('[]', { status: 200 })),
    )
    pinia = createPinia()
    setActivePinia(pinia)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  function mountComposer() {
    // attachTo:document 指令监听在 document 捕获阶段,游离 DOM 的事件到不了 document
    const wrapper = mount(Composer, {
      global: { plugins: [pinia], stubs: { RouterLink: true } },
      attachTo: document.body,
    })
    const chat = useChatStore(pinia)
    chat.skills = [{ name: 'demo-skill', description: '测试技能' }] as never
    return { wrapper, chat }
  }

  function pointerdownOn(el: Element): void {
    el.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
  }

  it('菜单项:先 pointerdown 再 click,选择生效且菜单收起', async () => {
    const { wrapper, chat } = mountComposer()

    await wrapper.find('.skill-pill').trigger('click')
    expect(wrapper.find('.wk-menu').exists()).toBe(true)

    const item = wrapper.find('.wk-menu .wk-mi')
    pointerdownOn(item.element) // 其他 wrapper 的 outside 指令在此触发——不得关闭本菜单
    await item.trigger('click')

    expect(chat.selectedSkill).toBe('demo-skill')
    expect(wrapper.find('.wk-menu').exists()).toBe(false)
  })

  it('pill 本身:pointerdown(外部其他指令) + click 完整开合', async () => {
    const { wrapper } = mountComposer()
    const pill = wrapper.find('.think-pill, .wk-pill:nth-of-type(1)')

    // 用思考 pill:先给"模型 wrapper"制造一次 pointerdown 外点(模拟另一个菜单开着)
    await wrapper.find('.skill-pill').trigger('click') // 先开技能菜单
    const thinkPill = wrapper.findAll('.wk-pill')[1]!
    pointerdownOn(thinkPill.element) // 技能菜单应在此收起,思考不受影响
    await thinkPill.trigger('click') // 切换到思考菜单
    expect(wrapper.find('.wk-menu').exists()).toBe(true)

    // 再点一次思考 pill:toggle 收起（pointerdown 不得先拆导致 click 又重开）
    pointerdownOn(thinkPill.element)
    await thinkPill.trigger('click')
    expect(wrapper.find('.wk-menu').exists()).toBe(false)
    void pill
  })

  it('点击输入框区域(pointerdown 外点)收起打开的菜单', async () => {
    const { wrapper } = mountComposer()

    await wrapper.find('.skill-pill').trigger('click')
    expect(wrapper.find('.wk-menu').exists()).toBe(true)

    pointerdownOn(wrapper.find('textarea').element)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.wk-menu').exists()).toBe(false)
  })
})
