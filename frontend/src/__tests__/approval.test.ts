import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ApprovalDialog from '@/components/ApprovalDialog.vue'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.unstubAllGlobals()
})

describe('chat store：审批状态（ticket 07）', () => {
  it('approval_request 设置 pendingApproval，approval_resolved 清除', () => {
    const chat = useChatStore()
    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr_1',
      tool: 'bash',
      params: { command: 'ls' },
    })
    expect(chat.pendingApproval).toEqual({ approvalId: 'apr_1', tool: 'bash', params: { command: 'ls' } })

    chat.handleEvent({ type: 'approval_resolved', approval_id: 'apr_1', allow: true })
    expect(chat.pendingApproval).toBeNull()
  })

  it('同一 approval_id 重发不重复设置（断线重连幂等）', () => {
    const chat = useChatStore()
    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr_1',
      tool: 'bash',
      params: { command: 'ls' },
    })
    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr_1',
      tool: 'bash',
      params: { command: 'ls' },
    })
    expect(chat.pendingApproval?.approvalId).toBe('apr_1')
    // 不同 id 排队（ADR-0005：并发会话审批各自挂起，弹窗取最早的）
    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr_2',
      tool: 'bash',
      params: { command: 'pwd' },
    })
    expect(chat.pendingApproval?.approvalId).toBe('apr_1')
    chat.handleEvent({ type: 'approval_resolved', approval_id: 'apr_1', allow: true })
    expect(chat.pendingApproval?.approvalId).toBe('apr_2')
  })

  it('approve 发送裁决命令并立即清弹窗', () => {
    class FakeWebSocket {
      static OPEN = 1
      readyState = 1
      sent: string[] = []
      send(data: string): void {
        this.sent.push(data)
      }
      close(): void {}
      onopen: unknown = null
      onmessage: unknown = null
      onclose: unknown = null
      onerror: unknown = null
    }
    vi.stubGlobal('WebSocket', FakeWebSocket)
    try {
      useConnectionStore().ensureConnected('tok')
      const chat = useChatStore()
      chat.handleEvent({
        type: 'approval_request',
        approval_id: 'apr_9',
        tool: 'bash',
        params: {},
      })
      chat.approve(false)
      expect(chat.pendingApproval).toBeNull()
    } finally {
      vi.unstubAllGlobals()
      useConnectionStore().disconnect()
    }
  })

  it('reset 清空审批状态', () => {
    const chat = useChatStore()
    chat.handleEvent({
      type: 'approval_request',
      approval_id: 'apr_1',
      tool: 'bash',
      params: {},
    })
    chat.reset()
    expect(chat.pendingApproval).toBeNull()
  })
})

describe('ApprovalDialog', () => {
  const approval = { approvalId: 'apr_1', tool: 'bash', params: { command: 'ls -la' } }

  it('展示工具名与参数，提供允许/拒绝', async () => {
    const wrapper = mount(ApprovalDialog, { props: { approval } })
    expect(wrapper.text()).toContain('bash')
    expect(wrapper.text()).toContain('ls -la')
    await wrapper.find('.allow').trigger('click')
    expect(wrapper.emitted('decide')).toEqual([[true]])
    await wrapper.find('.deny').trigger('click')
    expect(wrapper.emitted('decide')![1]).toEqual([false])
  })
})
