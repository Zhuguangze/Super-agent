<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Composer from '@/components/Composer.vue'
import MessageList from '@/components/MessageList.vue'
import ApprovalDialog from '@/components/ApprovalDialog.vue'
import SessionList from '@/components/SessionList.vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()

// ---- 侧栏宽度拖拽（localStorage 记忆） ----
const WIDTH_KEY = 'super_agent_sidebar_width'
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 264

const sidebarWidth = ref(clampWidth(Number(localStorage.getItem(WIDTH_KEY)) || SIDEBAR_DEFAULT))
const dragging = ref(false)
const sideEl = ref<HTMLElement | null>(null)

function clampWidth(w: number): number {
  return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(w)))
}

function onDragStart(ev: MouseEvent): void {
  ev.preventDefault()
  dragging.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onDragMove(ev: MouseEvent): void {
  if (!dragging.value || !sideEl.value) return
  const left = sideEl.value.getBoundingClientRect().left
  sidebarWidth.value = clampWidth(ev.clientX - left)
}

function onDragEnd(): void {
  if (!dragging.value) return
  dragging.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  try {
    localStorage.setItem(WIDTH_KEY, String(sidebarWidth.value))
  } catch {
    /* 存储不可用时静默——宽度仅本次会话生效 */
  }
}

function resetWidth(): void {
  sidebarWidth.value = SIDEBAR_DEFAULT
  try {
    localStorage.setItem(WIDTH_KEY, String(SIDEBAR_DEFAULT))
  } catch {
    /* 同上 */
  }
}

onMounted(() => {
  void chat.loadModels()
  void chat.loadUsage()
  void chat.loadSkills()
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
})

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
})

// ---- 空会话 hero 布局（claude.ai 式居中开场） ----
const isHero = computed(() => chat.messages.length === 0 && !chat.running)

// 开场语：每次会话切换/新建（sessionId 变化）且处于空会话时，从《中庸》四句随机取一——
// 不依赖浏览器刷新；人在 hero 页连点"+ 新对话"也会换句
const MOTTOES = ['致广大而尽精微', '极高明而道中庸', '温故而知新', '敦厚以崇礼'] as const
const motto = ref(MOTTOES[Math.floor(Math.random() * MOTTOES.length)]!)
watch(
  () => chat.currentSessionId,
  () => {
    if (isHero.value) motto.value = MOTTOES[Math.floor(Math.random() * MOTTOES.length)]!
  },
)

const HERO_CHIPS = [
  { icon: '⌨', label: '代码', prompt: '帮我写一个脚本，统计当前目录下各类型文件的数量。' },
  { icon: '✎', label: '撰写', prompt: '帮我写一封简短的工作周报，本周主要完成了项目里程碑。' },
  { icon: '❋', label: '创建', prompt: '帮我头脑风暴 5 个周末活动的创意。' },
  { icon: '⌕', label: '学习', prompt: '用通俗的语言解释 transformer 架构是怎么工作的。' },
  { icon: '☕', label: '日常生活', prompt: '帮我规划一份周末两天的作息与安排。' },
] as const
</script>

<template>
  <div class="wk">
    <div ref="sideEl" class="wk-side-slot" :style="{ width: `${sidebarWidth}px` }">
      <SessionList :style="{ width: '100%' }" />
    </div>
    <div
      class="wk-resizer"
      :class="{ dragging }"
      title="拖拽调整侧栏宽度 · 双击恢复默认"
      @mousedown="onDragStart"
      @dblclick="resetWidth"
    ></div>
    <main class="wk-main" :class="{ hero: isHero }">
      <MessageList
        v-if="!isHero"
        :messages="chat.messages"
        @branch="chat.branch"
        @switch-branch="chat.switchBranch"
      />
      <div v-if="isHero" class="hero-center">
        <div class="hero-greeting">
          <span class="hero-mark">✳</span>
          <span>{{ motto }}</span>
        </div>
      </div>
      <Composer
        :running="chat.running"
        :centered="isHero"
        @send="chat.sendPrompt"
        @abort="chat.abort"
      />
      <div v-if="isHero" class="hero-chips">
        <button
          v-for="chip in HERO_CHIPS"
          :key="chip.label"
          class="hero-chip"
          type="button"
          @click="chat.sendPrompt(chip.prompt)"
        >
          <span class="chip-icon">{{ chip.icon }}</span>{{ chip.label }}
        </button>
      </div>
      <ApprovalDialog
        v-if="chat.pendingApproval"
        :approval="chat.pendingApproval"
        @decide="chat.approve"
      />
    </main>
  </div>
</template>

<style scoped>
.wk{height:100%;display:flex}
.wk-side-slot{flex:none;height:100%;overflow:hidden}
.wk-resizer{flex:none;width:5px;cursor:col-resize;background:transparent;transition:background .15s;position:relative;z-index:10}
.wk-resizer:hover,.wk-resizer.dragging{background:var(--accent)}
.wk-main{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--surface)}
.wk-main.hero{justify-content:center}
.hero-center{display:flex;justify-content:center;padding:0 24px}
.hero-greeting{font-size:30px;font-weight:600;letter-spacing:-.01em;display:flex;align-items:center;gap:10px}
.hero-mark{color:var(--accent);font-size:26px}
.hero-chips{display:flex;justify-content:center;gap:9px;padding:18px 24px 40px;flex-wrap:wrap}
.hero-chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:999px;background:var(--surface);padding:6px 15px;font-size:12.5px;color:var(--ink2);transition:border-color .15s,color .15s}
.hero-chip:hover{border-color:var(--accent);color:var(--accent)}
.chip-icon{font-size:12px;color:var(--ink3)}
</style>
