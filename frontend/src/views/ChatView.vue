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
  clearMottoTimers()
})

// ---- 空会话 hero 布局（claude.ai 式居中开场） ----
const isHero = computed(() => chat.messages.length === 0 && !chat.running)

// ---- 专家聊天背景：按会话铭牌的专家身份映射背景图；未选专家用默认背景 ----
const EXPERT_BG: Record<string, string> = {
  研发专家: `${import.meta.env.BASE_URL}experts/expert-rd.png`,
  制造专家: `${import.meta.env.BASE_URL}experts/expert-mfg.png`,
  营销专家: `${import.meta.env.BASE_URL}experts/expert-mkt.png`,
}

const expertBg = computed<string | undefined>(() => {
  const sid = chat.currentSessionId
  if (!sid) return undefined
  const badge = chat.expertBySession[sid]
  return badge ? EXPERT_BG[badge.label] : undefined
})

// 开场语：每次会话切换/新建（sessionId 变化）且处于空会话时，从座右铭池随机取一——
// 不依赖浏览器刷新；人在 hero 页连点"+ 新对话"也会换句。逐字打字机呈现
const MOTTOES = ['致广大而尽精微',
'极高明而道中庸',
'温故而知新',
'敦厚以崇礼',
'知之愈明，则行之愈笃',
'以人为镜可以明得失',
'以铜为镜可以正衣冠',
'以古为镜可以知兴替',
'金石可镂',
'格物致知',
'穷理以致其知',
'知其然，亦知其所以然',
'工欲善其事，必先利其器',
'精益求精',
'业精于勤，荒于嬉',
'因势利导'] as const
const motto = ref('')
const mottoTyping = ref(false)
let mottoTimer: number | undefined
let mottoCycle: number | undefined
let mottoIdx = -1
const MOTTO_SPEED = 110 // 逐字间隔 ms
const MOTTO_PAUSE = 5000 // 打完后的停留 ms

function clearMottoTimers(): void {
  if (mottoTimer) {
    clearInterval(mottoTimer)
    mottoTimer = undefined
  }
  if (mottoCycle) {
    clearTimeout(mottoCycle)
    mottoCycle = undefined
  }
}

function typeMotto(text: string): void {
  clearMottoTimers()
  // 减少动态偏好：整句直接显示且不轮播（matchMedia 不存在的环境按无偏好处理）
  const reduced =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced) {
    motto.value = text
    mottoTyping.value = false
    return
  }
  motto.value = ''
  mottoTyping.value = true
  let i = 0
  mottoTimer = window.setInterval(() => {
    i += 1
    motto.value = text.slice(0, i)
    if (i >= text.length) {
      if (mottoTimer) clearInterval(mottoTimer)
      mottoTimer = undefined
      mottoTyping.value = false
      // 停留在当前页面：打完一句停 5s，自动换下一句逐字打出（一次只显示一句）
      mottoCycle = window.setTimeout(pickMotto, MOTTO_PAUSE)
    }
  }, MOTTO_SPEED)
}

function pickMotto(): void {
  let idx = Math.floor(Math.random() * MOTTOES.length)
  if (MOTTOES.length > 1) {
    while (idx === mottoIdx) idx = Math.floor(Math.random() * MOTTOES.length) // 避免连续重复
  }
  mottoIdx = idx
  typeMotto(MOTTOES[idx]!)
}

// 进入 hero（含首帧）开始打字；离开 hero（发起会话）停止轮播；hero 内切换会话立即换句
watch(
  isHero,
  (hero) => {
    if (hero) pickMotto()
    else clearMottoTimers()
  },
  { immediate: true },
)
watch(
  () => chat.currentSessionId,
  () => {
    if (isHero.value) pickMotto()
  },
)

/* 空会话专家卡（参考 R&D / P&M / Sales 专家卡样式）：hue 驱动卡片配色。
   点击不发任务——弹出专家名片（身份 + 能力），由用户决定是否以此身份开始会话。 */
const HERO_CHIPS = [
  {
    icon: 'flask',
    hue: '#3E8BFF',
    label: '研发专家',
    sub: '成分设计、材料性能、专利检索',
    identity: '资深材料与产品研发专家，覆盖从配方成分设计到专利布局的完整研发链路，熟悉实验设计、性能测试与文献调研。',
    caps: [
      '成分设计——配方建模、组分筛选与配比优化建议',
      '材料性能——指标解读、横向对比分析与改进方向',
      '专利检索——检索策略、权利要求解读与规避建议',
      '实验方案——DOE 设计、测试大纲与数据记录模板',
    ],
  },
  {
    icon: 'factory',
    hue: '#22B573',
    label: '制造专家',
    sub: '工艺路线、设备能力、生产成本',
    identity: '精益制造与工艺专家，从工艺路线设计、设备选型到量产成本核算，熟悉产线排布、SOP 沉淀与不良分析。',
    caps: [
      '工艺路线——工序拆解、流程编排与节拍估算',
      '设备能力——选型对比、产能与精度匹配',
      '生产成本——BOM 成本核算与降本路径',
      '质量控制——SOP 起草、质检要点与 8D 报告',
    ],
  },
  {
    icon: 'chart',
    hue: '#F59A23',
    label: '营销专家',
    sub: '客户需求、市场定位、交付约束',
    identity: '面向 B 端与 C 端的市场营销专家，从客户需求洞察到交付约束管理，擅长把技术卖点翻译成客户听得懂的语言。',
    caps: [
      '客户需求——访谈提纲、痛点归纳与客户画像',
      '市场定位——竞品分析、差异化卖点与定价思路',
      '交付约束——周期与条款梳理、履约风险提示',
      '营销内容——推广文案、销售话术与物料大纲',
    ],
  },
] as const

type HeroChip = (typeof HERO_CHIPS)[number]

/* 点击专家卡：钉上铭牌（persona 随每条消息注入 system prompt）+ 发送隐藏的自我介绍指令——
   触发词不入库、不显示气泡，回放时模型的开场介绍就是会话第一条消息 */
function startExpert(chip: HeroChip): void {
  chat.beginExpertSession({
    label: chip.label,
    hue: chip.hue,
    persona: `【${chip.label}】${chip.identity}\n能力范围：\n${chip.caps.map((c) => `- ${c}`).join('\n')}`,
  })
  chat.sendPrompt(
    `请以【${chip.label}】的身份向用户做自我介绍。` +
      `身份背景：${chip.identity}。` +
      `可以做的事情：${chip.caps.join('；')}。` +
      '要求：用中文回答；先用一句话表明身份，再分点介绍可以做的事情（以上述能力为主，可补充同领域典型任务）；' +
      '结尾用一句话引导用户直接说出需求；不要开始执行任何具体任务。',
    undefined,
    { hidden: true },
  )
}

/* Composer 发送接线：emit 的第三参 images → sendPrompt 的 opts.images */
function onSend(
  text: string,
  skill?: string,
  images?: { media_type: string; data: string }[],
): void {
  chat.sendPrompt(text, skill, { images })
}

const CHIP_ICONS: Record<(typeof HERO_CHIPS)[number]['icon'], string> = {
  flask:
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2v6.3L4.4 18.6a2 2 0 0 0 1.8 2.9h11.6a2 2 0 0 0 1.8-2.9L14 8.3V2"/><path d="M8.5 2h7"/><path d="M7.2 15h9.6"/></svg>',
  factory:
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21V9.5l5 3.2V9.5l5 3.2V9.5l5 3.2V21"/><path d="M3 21h18"/><path d="M17.5 9V3.5h3V9"/></svg>',
  chart:
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9.5 10.5 13.5 14.5 21 7"/><polyline points="15.5 7 21 7 21 12.5"/></svg>',
}
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
        :bg-image="expertBg"
        @branch="chat.branch"
        @switch-branch="chat.switchBranch"
      />
      <div v-if="isHero" class="hero-center">
        <div class="hero-greeting">
          <span class="hero-mark" :class="{ 'is-typing': mottoTyping }" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
              <!-- 固定底座：8 根等长臂，永远完整显示 -->
              <g>
                <line x1="12" y1="12" x2="12" y2="5" />
                <line x1="12" y1="12" x2="16.95" y2="7.05" />
                <line x1="12" y1="12" x2="19" y2="12" />
                <line x1="12" y1="12" x2="16.95" y2="16.95" />
                <line x1="12" y1="12" x2="12" y2="19" />
                <line x1="12" y1="12" x2="7.05" y2="16.95" />
                <line x1="12" y1="12" x2="5" y2="12" />
                <line x1="12" y1="12" x2="7.05" y2="7.05" />
              </g>
              <!-- 伸长段：从各自臂端向外延长，周期/相位互不相同 -->
              <line class="arm-ext" x1="12" y1="5" x2="12" y2="0.5" pathLength="1" style="--dur:2.2s;--delay:-0.2s" />
              <line class="arm-ext" x1="16.95" y1="7.05" x2="20.13" y2="3.87" pathLength="1" style="--dur:2.6s;--delay:-1.1s" />
              <line class="arm-ext" x1="19" y1="12" x2="23.5" y2="12" pathLength="1" style="--dur:2.0s;--delay:-0.7s" />
              <line class="arm-ext" x1="16.95" y1="16.95" x2="20.13" y2="20.13" pathLength="1" style="--dur:2.8s;--delay:-1.6s" />
              <line class="arm-ext" x1="12" y1="19" x2="12" y2="23.5" pathLength="1" style="--dur:2.3s;--delay:-0.5s" />
              <line class="arm-ext" x1="7.05" y1="16.95" x2="3.87" y2="20.13" pathLength="1" style="--dur:2.5s;--delay:-1.9s" />
              <line class="arm-ext" x1="5" y1="12" x2="0.5" y2="12" pathLength="1" style="--dur:2.1s;--delay:-1.3s" />
              <line class="arm-ext" x1="7.05" y1="7.05" x2="3.87" y2="3.87" pathLength="1" style="--dur:2.7s;--delay:-0.9s" />
            </svg>
          </span>
          <span>{{ motto }}</span><span v-if="mottoTyping" class="hero-cursor"></span>
        </div>
      </div>
      <Composer
        :running="chat.running"
        :centered="isHero"
        @send="onSend"
        @abort="chat.abort"
      />
      <div v-if="isHero" class="hero-chips">
        <button
          v-for="chip in HERO_CHIPS"
          :key="chip.label"
          class="hero-chip"
          type="button"
          :style="{ '--hue': chip.hue }"
          @click="startExpert(chip)"
        >
          <span class="chip-icon" v-html="CHIP_ICONS[chip.icon]"></span>
          <span class="chip-title">{{ chip.label }}</span>
          <span class="chip-sub">{{ chip.sub }}</span>
        </button>
      </div>
      <ApprovalDialog
        v-if="chat.pendingApproval"
        :approval="chat.pendingApproval"
        :session-label="
          chat.sessions.find((s) => s.id === chat.pendingApproval?.sessionId)?.title ??
          chat.pendingApproval?.sessionId
        "
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
.hero-mark{display:inline-block;color:var(--accent);transform-origin:50% 50%;animation:heroMarkSpin 24s linear infinite}
.hero-mark svg{display:block;overflow:visible}
/* 伸长段：从臂端（统一固定长度处）向外伸出/收回，各自周期/相位（--dur/--delay） */
.hero-mark .arm-ext{stroke-dasharray:1;stroke-dashoffset:1;animation:armStretch var(--dur,2.4s) ease-in-out infinite;animation-delay:var(--delay,0s)}
@keyframes armStretch{0%,100%{stroke-dashoffset:1}45%,60%{stroke-dashoffset:0}}
.hero-mark.is-typing svg{filter:drop-shadow(0 0 5px color-mix(in srgb,var(--accent) 60%,transparent))}
@keyframes heroMarkSpin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.hero-mark{animation:none}.hero-mark .arm-ext{animation:none;stroke-dashoffset:1}.hero-mark.is-typing svg{filter:none}}
.hero-cursor{display:inline-block;width:2px;height:.9em;margin-left:3px;vertical-align:-2px;background:var(--accent);animation:heroBlink 1s steps(1) infinite}
@keyframes heroBlink{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){.hero-cursor{display:none}}
.hero-chips{display:flex;justify-content:center;gap:10px;padding:14px 24px 40px;flex-wrap:wrap}
/* 实体按钮·炫酷版:对角双色渐变 + 顶缘高光 + hover 同色辉光 + 光带扫过 + 图标弹跳 */
.hero-chip{--hue:#3E8BFF;position:relative;overflow:hidden;display:flex;flex-direction:column;align-items:center;gap:1px;border:1px solid color-mix(in srgb,var(--hue) 28%,var(--line));border-radius:8px;background:linear-gradient(160deg,color-mix(in srgb,var(--hue) 16%,var(--surface)),color-mix(in srgb,var(--hue) 6%,var(--surface)) 55%,color-mix(in srgb,var(--hue) 12%,var(--surface)));padding:6px 14px 7px;min-width:126px;cursor:pointer;box-shadow:0 1px 2px color-mix(in srgb,var(--hue) 18%,transparent),0 2px 5px rgba(16,20,26,.07),inset 0 1px 0 color-mix(in srgb,white 55%,transparent);transition:box-shadow .18s,transform .18s,border-color .18s,filter .18s}
.hero-chip:hover{border-color:color-mix(in srgb,var(--hue) 50%,var(--line));filter:saturate(1.08);transform:translateY(-2px);box-shadow:0 2px 5px color-mix(in srgb,var(--hue) 26%,transparent),0 0 16px color-mix(in srgb,var(--hue) 32%,transparent),0 5px 12px rgba(16,20,26,.12),inset 0 1px 0 color-mix(in srgb,white 60%,transparent)}
.hero-chip:active{transform:translateY(0);box-shadow:0 1px 2px rgba(16,20,26,.1),inset 0 1px 2px color-mix(in srgb,var(--hue) 30%,transparent)}
/* 光带:hover 时一道斜向高光扫过按钮 */
.hero-chip::after{content:"";position:absolute;top:-45%;bottom:-45%;left:0;width:34%;background:linear-gradient(105deg,transparent,color-mix(in srgb,white 55%,transparent),transparent);transform:translateX(-160%) skewX(-18deg);pointer-events:none}
.hero-chip:hover::after{animation:chip-sheen .65s ease}
@keyframes chip-sheen{from{transform:translateX(-160%) skewX(-18deg)}to{transform:translateX(440%) skewX(-18deg)}}
.chip-icon{display:flex;color:color-mix(in srgb,var(--hue) 78%,var(--ink));transition:transform .28s cubic-bezier(.34,1.56,.64,1)}
.hero-chip:hover .chip-icon{transform:translateY(-1px) scale(1.16) rotate(-4deg)}
.chip-title{font-size:12px;font-weight:600;letter-spacing:.02em;color:var(--ink)}
.chip-sub{font-size:10px;letter-spacing:.01em;color:var(--ink3)}
@media (prefers-reduced-motion:reduce){.hero-chip::after{display:none}.hero-chip:hover{transform:none}.hero-chip:hover .chip-icon{transform:none}}
</style>
