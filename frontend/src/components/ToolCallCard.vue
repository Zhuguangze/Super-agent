<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ToolCallView } from '@/stores/chat'

const props = defineProps<{ tool: ToolCallView }>()
const expanded = ref(false)
// lightbox（票 24）：页内放大查看，点击遮罩关闭
const lightboxSrc = ref('')

const argsText = computed(() => JSON.stringify(props.tool.params))
const latestUpdate = computed(() => props.tool.updates[props.tool.updates.length - 1] ?? '')
const icon = computed(() =>
  props.tool.status === 'running' ? '⏳' : props.tool.status === 'error' ? '✕' : '✓',
)
const statusText = computed(() => {
  if (props.tool.status === 'running') return latestUpdate.value || '执行中…'
  if (props.tool.status === 'error') return '出错'
  return '✓ 完成'
})
const blocks = computed(() => props.tool.blocks ?? [])
const dataUri = (block: { media_type?: string; data?: string }): string =>
  `data:${block.media_type ?? 'image/png'};base64,${block.data ?? ''}`
</script>

<template>
  <div class="wk-card" :class="{ 'wk-card--error': tool.status === 'error' }" @click="expanded = !expanded">
    <span class="wk-card-ic" :class="{ pulse: tool.status === 'running' }">{{ icon }}</span>
    <span class="wk-card-name mono">{{ tool.name }}</span>
    <span class="wk-card-args">{{ tool.status === 'running' && latestUpdate ? latestUpdate : argsText }}</span>
    <span class="wk-card-status">{{ statusText }}</span>
    <!-- 富结果块（票 24）：常显（图是结果主体），与折叠详情解耦 -->
    <div v-if="blocks.length" class="card-blocks" @click.stop>
      <template v-for="(b, i) in blocks" :key="i">
        <img
          v-if="b.type === 'image'"
          class="block-img"
          :src="dataUri(b)"
          alt="工具返回的图片"
          @click="lightboxSrc = dataUri(b)"
        />
        <pre v-else-if="b.type === 'text'" class="detail-pre">{{ b.text }}</pre>
      </template>
    </div>
    <div v-if="expanded" class="wk-card-detail" @click.stop>
      <pre v-if="Object.keys(tool.params).length" class="detail-pre">{{ JSON.stringify(tool.params, null, 2) }}</pre>
      <pre v-if="tool.content" class="detail-pre" :class="{ err: tool.status === 'error' }">{{ tool.content }}</pre>
    </div>
    <div v-if="lightboxSrc" class="lightbox" @click="lightboxSrc = ''">
      <img :src="lightboxSrc" alt="放大查看" />
    </div>
  </div>
</template>

<style scoped>
.wk-card{border:1px solid var(--line2);background:var(--surface2);border-radius:10px;padding:9px 12px;display:flex;align-items:center;gap:9px;cursor:pointer;flex-wrap:wrap}
.wk-card--error{border-color:var(--error)}
.wk-card-ic{width:22px;height:22px;border-radius:6px;background:var(--surface);border:1px solid var(--line2);display:grid;place-items:center;font-size:11px;color:var(--ink2);flex:none;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.wk-card-name{flex:none;font-size:12px;font-weight:500}
.wk-card-args{flex:1;min-width:0;font-size:11.5px;color:var(--ink3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.wk-card-status{flex:none;font-size:11px;color:var(--ink2);display:inline-flex;gap:5px;align-items:center}
.wk-card--error .wk-card-status{color:var(--error)}
.wk-card-detail{flex-basis:100%;display:flex;flex-direction:column;gap:6px}
.detail-pre{margin:0;padding:8px 10px;border-radius:8px;background:#16181d;color:#e6e6e6;font-size:11.5px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.detail-pre.err{background:var(--error-soft);color:var(--error)}
/* 富结果块（票 24） */
.card-blocks{flex-basis:100%;display:flex;flex-direction:column;gap:6px}
.block-img{max-width:100%;max-height:180px;border-radius:8px;border:1px solid var(--line2);cursor:zoom-in;align-self:flex-start}
.lightbox{position:fixed;inset:0;background:rgba(10,12,16,.82);display:grid;place-items:center;z-index:200;cursor:zoom-out}
.lightbox img{max-width:92vw;max-height:92vh;border-radius:10px}
</style>
