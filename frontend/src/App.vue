<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { RouterView } from 'vue-router'
import HealthBadge from '@/components/HealthBadge.vue'
import ConnectionBadge from '@/components/ConnectionBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useConnectionStore } from '@/stores/connection'

const auth = useAuthStore()
const connection = useConnectionStore()
const chat = useChatStore()

function connectWith(token: string): void {
  connection.ensureConnected(token)
  // 下行事件统一交给 chat store（spec：所有前端消费同一条流）
  connection.setEventHandler((data) => chat.handleEvent(data))
}

onMounted(() => {
  if (auth.token) connectWith(auth.token)
})

watch(
  () => auth.token,
  (token) => {
    if (token) connectWith(token)
    else {
      connection.disconnect()
      chat.reset()
    }
  },
)
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <span class="brand">Super Agent</span>
      <div class="header-badges">
        <HealthBadge />
        <ConnectionBadge v-if="auth.token" />
      </div>
    </header>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.app-header {
  flex: none;
  display: flex;
  align-items: center;
  padding: 0.5rem 1.25rem;
  border-bottom: 1px solid var(--line2);
  background: var(--surface);
}

.brand {
  font-weight: 600;
  font-size: 0.95rem;
}

.brand em {
  font-style: normal;
  color: var(--accent);
}

.app-main {
  flex: 1;
  min-height: 0;
}
</style>
