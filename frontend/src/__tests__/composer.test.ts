import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Composer from '@/components/Composer.vue'
import { useChatStore } from '@/stores/chat'

function mountComposer() {
  setActivePinia(createPinia())
  const chat = useChatStore()
  const wrapper = mount(Composer, { props: { running: false } })
  return { wrapper, chat }
}

describe('Composer 技能选择（Claude Code 式，票 18 后续）', () => {
  it('技能 pill：菜单列出技能，选中落 chip，发送带 skill 名并清除选择', async () => {
    const { wrapper, chat } = mountComposer()
    chat.skills = [{ name: 'deploy', description: '部署清单' }]
    await wrapper.find('.skill-pill').trigger('click')
    const items = wrapper.findAll('.wk-menu .wk-mi')
    expect(items).toHaveLength(1)
    expect(items[0]!.text()).toContain('部署清单')
    await items[0]!.trigger('click')
    expect(wrapper.find('.skill-chip').text()).toContain('deploy')

    await wrapper.find('textarea').setValue('开始部署')
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    const emitted = wrapper.emitted('send')
    expect(emitted![0]).toEqual(['开始部署', 'deploy'])
    expect(chat.selectedSkill).toBe('') // 选择一次性：随消息消耗
    expect(wrapper.find('.skill-chip').exists()).toBe(false)
  })

  it('无选中时发送不带 skill', async () => {
    const { wrapper } = mountComposer()
    await wrapper.find('textarea').setValue('普通消息')
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')![0]).toEqual(['普通消息', undefined])
  })
})
