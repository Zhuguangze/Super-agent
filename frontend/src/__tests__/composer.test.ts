import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Composer from '@/components/Composer.vue'
import { fileToImageBlock } from '@/utils/image'
import { useChatStore } from '@/stores/chat'

function mountComposer(running = false) {
  setActivePinia(createPinia())
  const chat = useChatStore()
  const wrapper = mount(Composer, { props: { running } })
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

describe('Composer 图片附件（票 02）', () => {
  function pngFile(name = 'shot.png'): File {
    // PNG 魔数开头的最小字节串（内容对组件不透明，只走 base64 管线）
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4])
    return new File([bytes], name, { type: 'image/png' })
  }

  it('粘贴截图 → 预览出现 → 发送 emit 携带图片', async () => {
    const { wrapper } = mountComposer()
    const ta = wrapper.find('textarea')
    await ta.trigger('paste', { clipboardData: { files: [pngFile()] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))

    await ta.setValue('看这张图')
    await ta.trigger('keydown', { key: 'Enter' })
    const emitted = wrapper.emitted('send')!
    expect(emitted[0]![0]).toBe('看这张图')
    const images = emitted[0]![2] as { media_type: string; data: string }[]
    expect(images).toHaveLength(1)
    expect(images[0]!.media_type).toBe('image/png')
    // base64 可解码回原字节
    const binary = atob(images[0]!.data)
    expect(binary.length).toBe(12)
  })

  it('✕ 移除附件后发送不带图', async () => {
    const { wrapper } = mountComposer()
    const ta = wrapper.find('textarea')
    await ta.trigger('paste', { clipboardData: { files: [pngFile()] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))


    await wrapper.find('.wk-attach-thumb .attach-x').trigger('click')
    expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(0)

    await ta.setValue('纯文字')
    await ta.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')![0]![2]).toBeUndefined()
  })

  it('📎 按钮 + change 事件带入附件', async () => {
    const { wrapper } = mountComposer()
    await wrapper.find('.attach-pill').trigger('click') // jsdom 中 input.click() 为 no-op,不弹窗
    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [pngFile('picked.png')] })
    await input.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))
  })

  it('拖拽图片到输入框 → 附件出现', async () => {
    const { wrapper } = mountComposer()
    await wrapper.find('.wk-compose').trigger('drop', { dataTransfer: { files: [pngFile('drop.png')] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))
  })

  it('超 4 张 → 提示且只保留 4 张', async () => {
    const { wrapper } = mountComposer()
    await wrapper
      .find('textarea')
      .trigger('paste', { clipboardData: { files: [pngFile(), pngFile(), pngFile(), pngFile(), pngFile()] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(4))
    expect(wrapper.find('.attach-error').text()).toContain('最多 4 张')
  })

  it('单张超 5MB → 提示且不入列', async () => {
    const { wrapper } = mountComposer()
    const big = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'big.png', { type: 'image/png' })
    await wrapper.find('textarea').trigger('paste', { clipboardData: { files: [big] } })
    await vi.waitFor(() => expect(wrapper.find('.attach-error').text()).toContain('5MB'))
    expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(0)
  })

  it('非图片类型 → 提示且不入列', async () => {
    const { wrapper } = mountComposer()
    const bmp = new File([new Uint8Array([1])], 'pic.bmp', { type: 'image/bmp' })
    await wrapper.find('textarea').trigger('paste', { clipboardData: { files: [bmp] } })
    await vi.waitFor(() => expect(wrapper.find('.attach-error').text()).toContain('不支持'))
    expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(0)
  })

  it('纯图消息(无文字)可发送', async () => {
    const { wrapper } = mountComposer()
    await wrapper.find('textarea').trigger('paste', { clipboardData: { files: [pngFile()] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    const emitted = wrapper.emitted('send')!
    expect(emitted[0]![0]).toBe('')
    expect((emitted[0]![2] as unknown[]).length).toBe(1)
  })

  it('运行中带图发送 → 提示等待,不 emit', async () => {
    const { wrapper } = mountComposer(true)
    await wrapper.find('textarea').trigger('paste', { clipboardData: { files: [pngFile()] } })
    await vi.waitFor(() => expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1))
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toBeUndefined()
    expect(wrapper.find('.attach-error').text()).toContain('运行中')
    expect(wrapper.findAll('.wk-attach-thumb')).toHaveLength(1) // 附件保留
  })

  it('长边超过 2000px 的图 → 降采样后编码', async () => {
    vi.stubGlobal(
      'createImageBitmap',
      vi.fn(async () => ({ width: 4000, height: 3000, close() {} })),
    )
    const drawImage = vi.fn()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,c2NhbGVk')

    const block = await fileToImageBlock(pngFile('big.png'))
    expect(block.data).toBe('c2NhbGVk')
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2000, 1500)
    vi.restoreAllMocks()
  })

  it('computeTargetSize:长边不超限时返回 null', async () => {
    const { computeTargetSize } = await import('@/utils/image')
    expect(computeTargetSize(4000, 3000)).toEqual({ width: 2000, height: 1500 })
    expect(computeTargetSize(1000, 800)).toBeNull()
  })
})
