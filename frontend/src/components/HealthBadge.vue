<script setup lang="ts">
import { onMounted } from 'vue'
import { useHealthStore } from '@/stores/health'

const health = useHealthStore()

const LABELS = { unknown: '检测中', ok: '在线', down: '离线' } as const

onMounted(() => {
  void health.check()
})
</script>

<template>
  <span class="health-badge" :data-status="health.status">{{
    LABELS[health.status]
  }}</span>
</template>

<style scoped>
.health-badge {
  font-size: 0.8rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--color-border, #ddd);
}

.health-badge[data-status='ok'] {
  color: #15803d;
  border-color: #86efac;
}

.health-badge[data-status='down'] {
  color: #b91c1c;
  border-color: #fca5a5;
}
</style>
