<script setup lang="ts">
import { useConnectionStore } from '@/stores/connection'

const connection = useConnectionStore()

const LABELS = {
  disconnected: '未连接',
  connecting: '连接中',
  connected: '已连接',
  reconnecting: '重连中',
  unauthorized: '请重新登录',
} as const
</script>

<template>
  <span class="conn-badge" :data-status="connection.status">{{
    LABELS[connection.status]
  }}</span>
</template>

<style scoped>
.conn-badge {
  font-size: 0.8rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--color-border, #ddd);
}

.conn-badge[data-status='connected'] {
  color: #15803d;
  border-color: #86efac;
}

.conn-badge[data-status='reconnecting'],
.conn-badge[data-status='connecting'] {
  color: #a16207;
  border-color: #fde68a;
}

.conn-badge[data-status='unauthorized'],
.conn-badge[data-status='disconnected'] {
  color: #b91c1c;
  border-color: #fca5a5;
}
</style>
