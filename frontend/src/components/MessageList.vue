<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ChatMessage } from '@/stores/chat'
import { renderMarkdown } from '@/utils/markdown'
import ToolCallCard from '@/components/ToolCallCard.vue'

const props = defineProps<{ messages: ChatMessage[]; bgImage?: string }>()
const emit = defineEmits<{ branch: [entryId: string]; 'switch-branch': [siblingId: string] }>()

/* 用户图片 lightbox（票 03）：点击缩略图放大，点遮罩关闭 */
const lightbox = ref<{ media_type: string; data: string } | null>(null)

/* 专家聊天背景（票：专家身份背景图）：cover 随可视区域自适应铺满（无空带）,
   底部锚点保留画面最丰富的一段;纱罩保证文字可读,颜色随明暗主题自适应 */
const bgStyle = computed(() => {
  if (!props.bgImage) return undefined
  return {
    backgroundImage: `linear-gradient(color-mix(in srgb, var(--surface) 75%, transparent), color-mix(in srgb, var(--surface) 75%, transparent)), url('${props.bgImage}')`,
    backgroundSize: 'cover',
    backgroundPosition: 'center bottom',
    backgroundRepeat: 'no-repeat',
  }
})

/* 分支切换 pager（票 17）：分叉点消息在兄弟分支间步进 */
function prevSibling(msg: ChatMessage): string | undefined {
  const b = msg.branch
  return b ? b.siblings[b.index - 1] : undefined
}
function nextSibling(msg: ChatMessage): string | undefined {
  const b = msg.branch
  return b ? b.siblings[b.index + 1] : undefined
}

// 滚动管理：贴底跟随 + 回底按钮 + 挂载定位（刷新后历史回放场景）
const listEl = ref<HTMLElement | null>(null)
const atBottom = ref(true)

function scrollToBottom(smooth = false): void {
  const el = listEl.value
  if (!el) return
  if (typeof el.scrollTo === 'function') {
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  } else {
    el.scrollTop = el.scrollHeight // jsdom 无 scrollTo 的兜底
  }
}

function onScroll(): void {
  const el = listEl.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 48
}

watch(
  () => props.messages,
  () => {
    void nextTick(() => {
      if (atBottom.value) scrollToBottom()
    })
  },
  { deep: true },
)

onMounted(() => {
  // 刷新后历史回放：组件挂在 v-if 上，挂载即满列表——挂载时刻强制到底
  void nextTick(() => scrollToBottom())
})

function assistantHtml(msg: ChatMessage): string {
  return renderMarkdown(msg.text, msg.status === 'streaming')
}

/* 思考行（Claude Code 式）： Thinking… · N tokens  /  Thought for Xs */
function estTokens(text: string): number {
  if (!text) return 0
  const cjk = (text.match(/[一-鿿　-〿]/g) || []).length
  const rest = text.length - cjk
  return Math.max(1, Math.ceil(cjk + rest / 4))
}

const thinkState = (msg: ChatMessage) => {
  const streaming = msg.status === 'streaming'
  const hasThinking = !!msg.thinking
  const tokens = estTokens(msg.thinking)
  if (streaming && hasThinking) {
    return { label: 'Thinking…', meta: `· ${tokens} tokens`, live: true as const }
  }
  if (msg.thinkSeconds) {
    return { label: `Thought for ${msg.thinkSeconds}s`, meta: '', live: false as const }
  }
  if (!streaming && hasThinking) {
    return { label: 'Thought', meta: `· ${tokens} tokens`, live: false as const }
  }
  return { label: '思考过程', meta: '', live: false as const }
}

const anyThinkRow = computed(() => props.messages.some((m) => m.thinking))
</script>

<template>
  <div class="wk-list-wrap" :style="bgStyle">
    <div ref="listEl" class="wk-scroll" data-scroll @scroll="onScroll">
    <div class="wk-col">
      <template v-for="msg in messages" :key="msg.id">
        <details v-if="msg.role === 'assistant' && msg.thinking" class="wk-think" :class="{ live: thinkState(msg).live }">
          <summary>
            <span class="think-dot" :class="{ pulse: thinkState(msg).live }"></span>
            <span class="think-label">{{ thinkState(msg).label }}</span>
            <span v-if="thinkState(msg).meta" class="tmeta think-meta">{{ thinkState(msg).meta }}</span>
            <span class="chev">›</span>
          </summary>
          <p class="wk-think-body">{{ msg.thinking }}</p>
        </details>
        <div v-if="msg.role === 'user'" class="wk-msgrow">
          <div class="rowline">
            <button
              v-if="msg.entryId"
              class="fork-btn"
              title="从此条消息分叉新对话"
              @click="emit('branch', msg.entryId)"
            >⎇ 从此分叉</button>
            <div v-if="msg.branch && msg.branch.total > 1" class="branch-pager" title="切换此处的分支">
              <button
                class="pg pg-prev"
                :disabled="!prevSibling(msg)"
                @click="emit('switch-branch', prevSibling(msg)!)"
              >‹</button>
              <span class="pgn">{{ msg.branch.index + 1 }}/{{ msg.branch.total }}</span>
              <button
                class="pg pg-next"
                :disabled="!nextSibling(msg)"
                @click="emit('switch-branch', nextSibling(msg)!)"
              >›</button>
            </div>
          </div>
          <!-- 用户图片（票 03）：缩略图网格 + 点击放大 -->
          <div v-if="msg.images?.length" class="user-images">
            <button
              v-for="(img, i) in msg.images"
              :key="i"
              class="user-img"
              type="button"
              title="点击放大"
              @click="lightbox = img"
            >
              <img :src="`data:${img.media_type};base64,${img.data}`" alt="用户上传的图片" />
            </button>
          </div>
          <div v-if="msg.text" class="wk-user">{{ msg.text }}</div>
        </div>
        <div v-else-if="msg.role === 'assistant'" class="wk-msgrow">
          <div class="rowline">
            <button
              v-if="msg.entryId"
              class="fork-btn"
              title="从此条消息分叉新对话"
              @click="emit('branch', msg.entryId)"
            >⎇ 从此分叉</button>
            <div v-if="msg.branch && msg.branch.total > 1" class="branch-pager" title="切换此处的分支">
              <button
                class="pg pg-prev"
                :disabled="!prevSibling(msg)"
                @click="emit('switch-branch', prevSibling(msg)!)"
              >‹</button>
              <span class="pgn">{{ msg.branch.index + 1 }}/{{ msg.branch.total }}</span>
              <button
                class="pg pg-next"
                :disabled="!nextSibling(msg)"
                @click="emit('switch-branch', nextSibling(msg)!)"
              >›</button>
            </div>
          </div>
          <div class="wk-asst">
            <div class="bubble" v-html="assistantHtml(msg)"></div>
            <div
              v-if="msg.status === 'streaming' && !msg.text && !msg.thinking"
              class="pending"
            >正在生成…</div>
            <span v-if="msg.status === 'aborted'" class="flag">已中止</span>
            <span v-if="msg.usage" class="tmeta usage-tag">{{ msg.usage.input_tokens }}<template v-if="msg.usage.cache_read_tokens > 0"> · cached {{ msg.usage.cache_read_tokens }}</template> → {{ msg.usage.output_tokens }} tokens</span>
          </div>
        </div>
        <ToolCallCard v-else-if="msg.role === 'tool' && msg.tool" :tool="msg.tool" />
        <p v-else-if="msg.role === 'notice'" class="wk-note">{{ msg.text }}</p>
        <div v-else-if="msg.role === 'error'" class="wk-error">{{ msg.text }}</div>
      </template>
      <p v-if="messages.length === 0" class="empty">发送第一条消息，看看 Agent 能做什么。</p>
    </div>
    </div>
    <button
      v-if="!atBottom"
      class="jump-bottom"
      title="回到最新消息"
      @click="scrollToBottom(true)"
    >↓</button>
    <!-- 图片放大查看（票 03）：点遮罩任意处关闭 -->
    <div v-if="lightbox" class="img-lightbox" @click="lightbox = null">
      <img :src="`data:${lightbox.media_type};base64,${lightbox.data}`" alt="图片放大查看" />
    </div>
  </div>
</template>

<style scoped>
.wk-list-wrap{position:relative;flex:1;min-height:0;display:flex}
.wk-scroll{flex:1;overflow-y:auto;min-height:0}
.jump-bottom{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);width:36px;height:36px;border-radius:50%;border:1px solid var(--line2);background:var(--surface);color:var(--ink2);font-size:15px;cursor:pointer;display:grid;place-items:center;box-shadow:var(--shadow);transition:border-color .15s,color .15s;z-index:5;padding:0}
.jump-bottom:hover{border-color:var(--accent);color:var(--accent)}
.wk-col{max-width:720px;margin:0 auto;padding:26px 24px 24px;display:flex;flex-direction:column;gap:16px}
.wk-msgrow{display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.wk-msgrow:has(.wk-asst){align-items:flex-start}
.wk-user{max-width:78%;background:var(--accent-soft);border-radius:14px 14px 4px 14px;padding:9px 14px;font-size:14px;line-height:1.65;white-space:pre-wrap;word-break:break-word}
/* 用户图片网格 + lightbox（票 03） */
.user-images{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;max-width:78%}
.user-img{width:120px;height:120px;border:1px solid var(--line);border-radius:10px;overflow:hidden;padding:0;cursor:zoom-in;background:var(--surface2)}
.user-img img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .15s}
.user-img:hover img{transform:scale(1.04)}
.img-lightbox{position:fixed;inset:0;z-index:300;display:grid;place-items:center;background:rgba(8,12,18,.72);backdrop-filter:blur(2px);cursor:zoom-out;padding:24px}
.img-lightbox img{max-width:min(920px,92vw);max-height:88vh;border-radius:12px;box-shadow:var(--shadow)}
.fork-btn{border:none;background:transparent;color:var(--ink3);font-size:11px;cursor:pointer;padding:0 4px;opacity:0;transition:opacity .12s}
.wk-msgrow:hover .fork-btn{opacity:1}
.fork-btn:hover{color:var(--accent)}
.rowline{display:flex;align-items:center;gap:6px}
/* 分支切换 pager（票 17）：常显（区别于 hover 才出现的分叉按钮） */
.branch-pager{display:inline-flex;align-items:center;font-size:11px;color:var(--ink3)}
.branch-pager .pg{border:none;background:transparent;color:var(--ink3);cursor:pointer;padding:0 3px;font-size:13px;line-height:1}
.branch-pager .pg:hover:not(:disabled){color:var(--accent)}
.branch-pager .pg:disabled{opacity:.3;cursor:default}
.branch-pager .pgn{font-variant-numeric:tabular-nums;min-width:28px;text-align:center}
.wk-think{border-left:2px solid var(--line);padding:2px 0 2px 12px;color:var(--ink2);font-size:12.5px;line-height:1.65}
.wk-think summary{cursor:pointer;user-select:none;color:var(--ink2);display:flex;align-items:center;gap:8px;list-style:none}
.wk-think summary::-webkit-details-marker{display:none}
.think-dot{width:7px;height:7px;border-radius:50%;background:var(--ink3);flex:none}
.think-dot.pulse{background:var(--accent);animation:pulse 1.6s infinite}
.think-label{font-weight:500;color:var(--ink2)}
.think-meta{color:var(--ink3)}
.wk-think .chev{margin-left:auto;color:var(--ink3);font-size:13px;transform:rotate(90deg);transition:transform .15s}
.wk-think[open] .chev{transform:rotate(270deg)}
.wk-think-body{white-space:pre-wrap;margin:8px 0 2px;color:var(--ink2)}
.wk-asst{font-size:14px;line-height:1.7;display:flex;flex-direction:column;gap:8px}
.wk-asst :deep(p){margin:0 0 8px}
.wk-asst :deep(p:last-child){margin-bottom:0}
.wk-asst :deep(pre){overflow-x:auto;padding:10px 12px;border-radius:10px;background:#16181d;color:#e6e6e6}
.wk-asst :deep(code){font-size:.9em;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.wk-asst :deep(pre code){background:none;padding:0}
.pending{font-size:12.5px;color:var(--ink3);font-style:italic}
.flag{font-size:11.5px;color:var(--ink3)}
.usage-tag{font-size:10.5px;color:var(--ink3)}
.wk-note{margin:0;font-size:12px;color:var(--ink3);font-style:italic;text-align:center}
.wk-error{align-self:center;max-width:90%;padding:7px 14px;border:1px solid var(--error);border-radius:10px;background:var(--error-soft);color:var(--error);font-size:12.5px}
.empty{margin:auto;color:var(--ink3);font-size:13px}
</style>
