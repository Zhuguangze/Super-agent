<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChatStore } from '@/stores/chat'

const props = defineProps<{ running: boolean; centered?: boolean }>()
const emit = defineEmits<{ send: [text: string, skill?: string]; abort: [] }>()

const chat = useChatStore()
const draft = ref('')
const openMenu = ref<'model' | 'think' | 'skill' | null>(null)

// ---- 当前会话上下文用量（最后一轮真实 input_tokens / 模型上下文窗口） ----
const ctxUsed = computed(() => {
  for (let i = chat.messages.length - 1; i >= 0; i--) {
    const m = chat.messages[i]
    if (m && m.role === 'assistant' && m.usage) return m.usage.input_tokens + m.usage.output_tokens
  }
  return 0
})

const ctxTotal = computed(() => {
  const m = (chat.models ?? []).find((x) => x.id === chat.currentModel)
  return m?.context_window ?? 0
})

const ctxPct = computed(() =>
  ctxTotal.value > 0 ? Math.min(100, Math.round((ctxUsed.value / ctxTotal.value) * 100)) : 0,
)

const THINK_ZH: Record<string, string> = { low: '低', medium: '中', high: '高', xhigh: '超高', max: '最大' }
const THINK_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const

const placeholder = computed(() =>
  props.running ? '插话 (Steer) — 直接进入当前 Loop…' : '给 Super Agent 一个任务…',
)
const canSend = computed(() => !!draft.value.trim() || props.running)

function closeMenus(): void {
  openMenu.value = null
}

function toggleMenu(kind: 'model' | 'think' | 'skill'): void {
  openMenu.value = openMenu.value === kind ? null : kind
}

function submit(): void {
  const text = draft.value.trim()
  if (!text) return
  emit('send', text, chat.selectedSkill || undefined)
  chat.selectedSkill = '' // 选择一次性：随本条消息消耗（store 侧幂等）
  draft.value = ''
  closeMenus()
}

function pickSkill(name: string): void {
  chat.selectedSkill = chat.selectedSkill === name ? '' : name
  closeMenus()
}

function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    submit()
  }
  if (ev.key === 'Escape') closeMenus()
}

function onInput(ev: Event): void {
  const ta = ev.target as HTMLTextAreaElement
  ta.style.height = 'auto'
  ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
}

function modelName(id: string): string {
  return (chat.models ?? []).find((m) => m.id === id)?.name ?? id
}

function pickModel(id: string): void {
  chat.setModel(id)
  closeMenus()
}

function pickThink(level: 'low' | 'medium' | 'high' | 'xhigh' | 'max'): void {
  chat.setThinkingLevel(level)
  closeMenus()
}
</script>

<template>
  <div class="wk-compose-wrap" :class="{ centered }" @keydown="onKeydown">
    <div class="wk-compose-box">
      <div v-if="chat.pendingCount > 0" class="wk-queue">
        ⏸ <span>{{ chat.pendingCount }} 条已排队 — 当前任务结束后自动注入</span>
      </div>
      <div v-if="chat.selectedSkill" class="wk-queue skill-chip">
        ⚡ <span>使用技能：{{ chat.selectedSkill }}</span>
        <button class="chip-x" type="button" title="取消选择" @click="chat.selectedSkill = ''">✕</button>
      </div>
      <div class="wk-compose">
        <textarea
          v-model="draft"
          rows="1"
          :placeholder="placeholder"
          @input="onInput"
        ></textarea>
        <div class="wk-tbar" @clickout.stop>
          <span class="wk-mw">
            <button
              class="wk-pill model-pill"
              type="button"
              :title="`会话上下文：约 ${ctxUsed} / ${ctxTotal} tokens（${ctxPct}%）`"
              @click="toggleMenu('model')"
            >
              <span
                v-if="ctxTotal > 0 && ctxPct > 0"
                class="water"
                :class="{ near: ctxPct >= 80 }"
                :style="{ height: `${ctxPct}%` }"
                aria-hidden="true"
              >
                <svg class="wavesvg w1" viewBox="0 0 120 10" preserveAspectRatio="none">
                  <path d="M0 5 Q 15 0 30 5 T 60 5 T 90 5 T 120 5 V 10 H 0 Z" />
                </svg>
                <svg class="wavesvg w2" viewBox="0 0 120 10" preserveAspectRatio="none">
                  <path d="M0 5 Q 15 2 30 5 T 60 5 T 90 5 T 120 5 V 10 H 0 Z" />
                </svg>
              </span>
              <span class="lbl">{{ modelName(chat.currentModel) }}</span><span class="cv">▾</span>
            </button>
            <div v-if="openMenu === 'model'" class="wk-menu">
              <p v-if="chat.models.length === 0" class="menu-empty">
                尚未添加任何模型——到 <RouterLink class="mlink" to="/settings">设置页</RouterLink> 添加模型配置后即可对话。
              </p>
              <button
                v-for="m in chat.models"
                :key="m.id"
                class="wk-mi"
                type="button"
                @click="pickModel(m.id)"
              >
                <span class="ck">{{ m.id === chat.currentModel ? '✓' : '' }}</span>
                <span class="mi-body">
                  <span class="nm">{{ m.name }}</span>
                  <span class="sub">{{ m.id }} · {{ Math.round(m.context_window / 1000) }}k ctx</span>
                </span>
                <span class="sp"></span>
                <span v-if="m.configured === false" class="se-badge warn-badge">未配置</span>
                <span v-else class="se-badge">anthropic</span>
              </button>
              <div class="wk-mfoot">切换即刻生效（WS set_model，下条消息起用）。<RouterLink class="mlink" to="/settings">管理模型 → Settings</RouterLink></div>
            </div>
          </span>
          <span class="wk-mw">
            <button
              class="wk-pill"
              type="button"
              title="Thinking level — set_thinking_level"
              @click="toggleMenu('think')"
            >
              <span class="sic">✦</span><span class="lbl">思考 · {{ THINK_ZH[chat.currentEffort] }}</span><span class="cv">▾</span>
            </button>
            <div v-if="openMenu === 'think'" class="wk-menu">
              <button
                v-for="level in THINK_LEVELS"
                :key="level"
                class="wk-mi"
                type="button"
                @click="pickThink(level)"
              >
                <span class="ck">{{ level === chat.currentEffort ? '✓' : '' }}</span>
                <span class="mi-body">
                  <span class="nm">思考 · {{ THINK_ZH[level] }}</span>
                  <span class="sub">effort: {{ level }}</span>
                </span>
              </button>
              <div class="wk-mfoot">adaptive thinking — set_thinking_level 随时调整。</div>
            </div>
          </span>
          <span class="wk-mw">
            <button
              class="wk-pill skill-pill"
              type="button"
              title="技能——手动指定本次使用的 Skill（Claude Code 式）"
              @click="toggleMenu('skill')"
            >
              <span class="sic">⚡</span><span class="lbl">技能</span><span class="cv">▾</span>
            </button>
            <div v-if="openMenu === 'skill'" class="wk-menu">
              <button
                v-for="s in chat.skills"
                :key="s.name"
                class="wk-mi"
                type="button"
                @click="pickSkill(s.name)"
              >
                <span class="ck">{{ s.name === chat.selectedSkill ? '✓' : '' }}</span>
                <span class="mi-body">
                  <span class="nm">{{ s.name }}</span>
                  <span class="sub">{{ s.description }}</span>
                </span>
              </button>
              <p v-if="chat.skills.length === 0" class="menu-empty">
                尚无技能——在服务端 <code>~/.super-agent/skills/&lt;技能名&gt;/SKILL.md</code> 放置技能文件（frontmatter 含 description），无需重启。
              </p>
              <div class="wk-mfoot">选中后随下一条消息全文注入，确定性生效；再点一次取消。</div>
            </div>
          </span>
          <span class="sp"></span>
          <button
            v-if="running"
            class="wk-send stop"
            type="button"
            title="中断 (abort)"
            @click="emit('abort')"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/></svg>
          </button>
          <button
            v-else
            class="wk-send"
            type="button"
            title="发送 (Enter)"
            :disabled="!canSend"
            @click="submit"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wk-compose-wrap{flex:none;padding:0 22px 26px;background:var(--surface)}
.wk-compose-wrap.centered{padding:0;background:transparent}
.wk-compose-box{max-width:720px;margin:0 auto}
.wk-queue{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--ink2);background:var(--surface2);border:1px solid var(--line2);border-radius:999px;padding:4px 12px;margin-bottom:8px}
.skill-chip .chip-x{border:none;background:transparent;color:var(--ink3);cursor:pointer;font-size:11px;padding:0 2px}
.skill-chip .chip-x:hover{color:var(--error)}
.wk-compose{position:relative;border:1px solid var(--line);border-radius:20px;background:var(--surface2);padding:13px 13px 9px;transition:border-color .15s,box-shadow .15s}
.wk-compose:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.wk-compose textarea{display:block;width:100%;border:none;background:transparent;outline:none;resize:none;font-size:14.5px;line-height:1.6;max-height:160px;padding:2px 4px 0;color:var(--ink);font-family:inherit}
.wk-compose textarea::placeholder{color:var(--ink3)}
.wk-tbar{display:flex;align-items:center;gap:5px;margin-top:9px}
.wk-tbar .sp{flex:1}
.wk-mw{position:relative;flex:none}
.ctx-bar{margin-top:4px;height:3px;border-radius:2px;background:var(--line);overflow:hidden}
.ctx-fill{height:100%;background:var(--accent);border-radius:2px;transition:width .3s}
.ctx-fill.near{background:var(--warn)}
.wk-pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:5px 11px;font-size:12.5px;color:var(--ink2);white-space:nowrap;transition:border-color .15s,color .15s}
.wk-pill:hover{border-color:var(--ink3);color:var(--ink)}
.wk-pill .lbl{font-weight:500;color:var(--ink)}
.wk-pill .cv{font-size:9px;color:var(--ink3);transform:translateY(1px)}
.wk-pill .sic{color:var(--accent);font-size:12px}
/* 模型 pill 注水效果：水面从底部随上下文用量上涨，两层滚动正弦波制造波动水面 */
.model-pill{position:relative;overflow:hidden}
.model-pill .lbl,.model-pill .cv{position:relative;z-index:1}
.water{position:absolute;left:0;right:0;bottom:0;height:0%;background:var(--accent);opacity:.16;transition:height .9s ease;overflow:hidden;pointer-events:none}
.water.near{background:var(--warn);opacity:.22}
.wavesvg{position:absolute;left:0;top:-9px;width:200%;height:10px;display:block}
.wavesvg path{fill:var(--accent)}
.wavesvg.w1{animation:waveMove 5s linear infinite}
.wavesvg.w2{top:-8px;opacity:.55;animation:waveMoveBack 8s linear infinite}
.water.near .wavesvg path{fill:var(--warn)}
@keyframes waveMove{to{transform:translateX(-50%)}}
@keyframes waveMoveBack{from{transform:translateX(-50%)}to{transform:translateX(0)}}
@media (prefers-reduced-motion: reduce){.wavesvg{animation:none}}
.wk-send{width:32px;height:32px;border-radius:999px;background:var(--accent);color:var(--on-accent);display:grid;place-items:center;flex:none;transition:opacity .15s,filter .15s}
.wk-send svg{width:15px;height:15px}
.wk-send:hover{filter:brightness(1.07)}
.wk-send[disabled]{opacity:.32;cursor:default;filter:none}
.wk-send.stop{background:var(--ink);color:var(--ground)}
.wk-menu{position:absolute;bottom:calc(100% + 8px);left:0;min-width:264px;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:5px;z-index:60}
.wk-mi{display:flex;align-items:center;gap:9px;width:100%;text-align:left;padding:8px 10px;border-radius:9px;font-size:13px;color:var(--ink)}
.wk-mi:hover{background:var(--surface2)}
.wk-mi .ck{width:14px;color:var(--accent);flex:none;font-size:12px}
.wk-mi .mi-body{min-width:0}
.wk-mi .nm{display:block;font-size:13px;line-height:1.35}
.wk-mi .sub{display:block;font-size:10.5px;color:var(--ink3);font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;line-height:1.4}
.wk-mi .sp{flex:1}
.wk-mfoot{border-top:1px solid var(--line2);margin-top:4px;padding:7px 10px 5px;font-size:11px;color:var(--ink3);line-height:1.6}
.menu-empty{margin:0;padding:10px 12px;font-size:12.5px;color:var(--ink2);line-height:1.7}
.mlink{color:var(--accent);text-decoration:none;cursor:pointer;display:inline-block}
.se-badge{font-size:10.5px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--ink3);white-space:nowrap}
.warn-badge{color:var(--warn);border-color:var(--warn)}
</style>
