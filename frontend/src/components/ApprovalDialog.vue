<script setup lang="ts">
import { computed } from 'vue'
import type { PendingApproval } from '@/stores/chat'

const props = defineProps<{ approval: PendingApproval; sessionLabel?: string }>()
const emit = defineEmits<{ decide: [allow: boolean] }>()

const paramsText = computed(() => JSON.stringify(props.approval.params, null, 2))
</script>

<template>
  <div class="approval-mask" role="dialog" aria-modal="true">
    <div class="approval-dialog">
      <h3 class="approval-title">工具调用待确认</h3>
      <p v-if="sessionLabel" class="approval-source">来自会话：{{ sessionLabel }}</p>
      <p class="approval-tool">
        Agent 请求执行 <code>{{ approval.tool }}</code>，请确认：
      </p>
      <pre class="approval-params">{{ paramsText }}</pre>
      <div class="approval-actions">
        <button class="deny" @click="emit('decide', false)">拒绝</button>
        <button class="allow" @click="emit('decide', true)">允许执行</button>
      </div>
      <p class="approval-hint">不确认将在超时后自动拒绝</p>
    </div>
  </div>
</template>

<style scoped>
.approval-mask {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 40%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}

.approval-dialog {
  width: min(480px, calc(100vw - 2rem));
  background: var(--color-bg, #fff);
  border: 1px solid var(--color-border, #e3e5e8);
  border-radius: 0.75rem;
  padding: 1.1rem 1.25rem;
  box-shadow: 0 12px 40px rgb(0 0 0 / 18%);
}

.approval-title {
  margin: 0 0 0.4rem;
  font-size: 1rem;
}

/* 来源会话标注（ADR-0005）：并发会话下审批可能来自后台会话 */
.approval-source {
  margin: 0 0 0.4rem;
  font-size: 0.78rem;
  color: var(--color-text-muted, #6b7280);
}

.approval-tool {
  margin: 0 0 0.6rem;
  font-size: 0.9rem;
}

.approval-tool code {
  font-weight: 700;
}

.approval-params {
  margin: 0 0 0.9rem;
  padding: 0.5rem;
  border-radius: 0.4rem;
  background: #16181d;
  color: #e6e6e6;
  font-size: 0.8rem;
  max-height: 10rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.approval-actions button {
  padding: 0.45rem 1rem;
  border: none;
  border-radius: 0.5rem;
  font: inherit;
  cursor: pointer;
}

.approval-actions .allow {
  background: var(--color-brand, #4f6ef7);
  color: #fff;
}

.approval-actions .deny {
  background: transparent;
  border: 1px solid var(--color-border, #e3e5e8);
  color: inherit;
}

.approval-hint {
  margin: 0.6rem 0 0;
  font-size: 0.75rem;
  color: var(--color-text-muted, #6b7280);
}
</style>
