<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const mode = ref<'login' | 'register'>('login')
const showPwd = ref(false)
const typed = ref('')
const statsEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)

const WORDS = [
  '排查 tests/readiness NaN 的原因',
  '整理本周周报并生成要点',
  '给登录页加自定义模型入口',
  '压缩 3 个长会话的记忆',
]

let raf = 0
let typeTimer: number | undefined
let cleanupResize: (() => void) | undefined

/* 记住当前用户：登录成功后保存，回到登录页自动回填（登出不清除）。
   密码仅做 base64 编码防肩窥/明文检索，不是加密——内网单机自用取舍。 */
const REMEMBER_USER_KEY = 'super_agent_remember_user'
const REMEMBER_PWD_KEY = 'super_agent_remember_pwd'

function saveRemember(name: string, pwd: string): void {
  try {
    localStorage.setItem(REMEMBER_USER_KEY, name)
    localStorage.setItem(REMEMBER_PWD_KEY, btoa(String.fromCharCode(...new TextEncoder().encode(pwd))))
  } catch {
    /* localStorage 不可用时静默跳过 */
  }
}

function loadRemember(): void {
  try {
    const name = localStorage.getItem(REMEMBER_USER_KEY)
    const pwd = localStorage.getItem(REMEMBER_PWD_KEY)
    if (name) username.value = name
    if (pwd) password.value = new TextDecoder().decode(Uint8Array.from(atob(pwd), (c) => c.charCodeAt(0)))
  } catch {
    /* 记住的密码损坏时忽略，让用户重新输入 */
  }
}

async function submit(): Promise<void> {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    if (mode.value === 'register') {
      await auth.register(username.value.trim(), password.value)
      await auth.login(username.value.trim(), password.value)
    } else {
      await auth.login(username.value.trim(), password.value)
    }
    saveRemember(username.value.trim(), password.value)
    router.push({ name: 'home' })
  } catch (e) {
    error.value = e instanceof Error ? (e.message.includes('409') ? '用户名已被占用' : e.message.includes('401') ? '用户名或密码错误' : e.message) : '登录失败'
  } finally {
    busy.value = false
  }
}

/* agent 网络脉冲背景（原型 lgStart 的 Vue 移植） */
function startScene(): void {
  const cv = canvasEl.value
  if (!cv) return
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const LG = '69,196,216'
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const rm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  let W = 0
  let H = 0
  const nodes: { x: number; y: number; vx: number; vy: number; r: number }[] = []
  const pulses: { a: number; b: number; t: number; s: number }[] = []
  let links = 0
  let mx = 0.5
  let my = 0.5
  let lastSpawn = 0
  let lastStat = 0

  const resize = (): void => {
    W = cv.clientWidth
    H = cv.clientHeight
    cv.width = W * dpr
    cv.height = H * dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }
  resize()
  const onResize = (): void => resize()
  window.addEventListener('resize', onResize)
  const onMouse = (ev: MouseEvent): void => {
    mx = ev.clientX / window.innerWidth
    my = ev.clientY / window.innerHeight
  }
  window.addEventListener('mousemove', onMouse)
  cleanupResize = () => {
    window.removeEventListener('resize', onResize)
    window.removeEventListener('mousemove', onMouse)
  }

  const N = Math.max(40, Math.min(96, Math.round(window.innerWidth * window.innerHeight / 15000)))
  for (let i = 0; i < N; i++) {
    nodes.push({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - 0.5) * 0.24, vy: (Math.random() - 0.5) * 0.24, r: Math.random() * 1.5 + 0.6 })
  }
  const LINK = 150
  const spawn = (): void => {
    const i = (Math.random() * nodes.length) | 0
    const j = (i + 1 + ((Math.random() * (nodes.length - 1)) | 0)) % nodes.length
    const a = nodes[i]!
    const b = nodes[j]!
    const dx = a.x - b.x
    const dy = a.y - b.y
    if (dx * dx + dy * dy < LINK * LINK * 2.4) pulses.push({ a: i, b: j, t: 0, s: 0.011 + Math.random() * 0.013 })
  }
  const paint = (ts: number): void => {
    ctx.clearRect(0, 0, W, H)
    const ox = (mx - 0.5) * 24
    const oy = (my - 0.5) * 18
    links = 0
    ctx.lineWidth = 1
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]!
        const b = nodes[j]!
        const dx = a.x - b.x
        const dy = a.y - b.y
        const d2 = dx * dx + dy * dy
        if (d2 < LINK * LINK) {
          links++
          ctx.strokeStyle = `rgba(${LG},${((1 - Math.sqrt(d2) / LINK) * 0.15).toFixed(3)})`
          ctx.beginPath()
          ctx.moveTo(a.x + ox, a.y + oy)
          ctx.lineTo(b.x + ox, b.y + oy)
          ctx.stroke()
        }
      }
    }
    for (const p of pulses) {
      if (!rm) p.t += p.s
      const a = nodes[p.a]!
      const b = nodes[p.b]!
      const t0 = Math.max(0, p.t - 0.2)
      const x2 = a.x + ox + (b.x - a.x) * p.t
      const y2 = a.y + oy + (b.y - a.y) * p.t
      ctx.strokeStyle = `rgba(${LG},${(0.5 * (1 - p.t * 0.55)).toFixed(3)})`
      ctx.lineWidth = 1.4
      ctx.beginPath()
      ctx.moveTo(a.x + ox + (b.x - a.x) * t0, a.y + oy + (b.y - a.y) * t0)
      ctx.lineTo(x2, y2)
      ctx.stroke()
      ctx.fillStyle = 'rgba(199,241,248,.95)'
      ctx.beginPath()
      ctx.arc(x2, y2, 2.2, 0, 7)
      ctx.fill()
      ctx.lineWidth = 1
    }
    for (let k = pulses.length - 1; k >= 0; k--) if (pulses[k]!.t >= 1) pulses.splice(k, 1)
    ctx.fillStyle = `rgba(${LG},.6)`
    for (const n of nodes) {
      ctx.beginPath()
      ctx.arc(n.x + ox, n.y + oy, n.r, 0, 7)
      ctx.fill()
      if (!rm) {
        n.x += n.vx
        n.y += n.vy
        if (n.x < -8) n.x = W + 8
        else if (n.x > W + 8) n.x = -8
        if (n.y < -8) n.y = H + 8
        else if (n.y > H + 8) n.y = -8
      }
    }
    if (!rm) {
      if (ts - lastSpawn > 260) {
        lastSpawn = ts
        if (pulses.length < 8) spawn()
      }
      if (ts - lastStat > 600) {
        lastStat = ts
        if (statsEl.value) statsEl.value.textContent = `NODES ${nodes.length} · LINK ${links} · EVT ${pulses.length}`
      }
    } else if (statsEl.value) {
      statsEl.value.textContent = `NODES ${nodes.length} · LINK ${links} · EVT 0`
    }
  }
  const frame = (ts: number): void => {
    raf = requestAnimationFrame(frame)
    paint(ts)
  }
  if (rm) paint(0)
  else raf = requestAnimationFrame(frame)

  // 打字机任务行
  if (!rm) {
    let wi = 0
    let ci = 0
    let del = false
    let hold = 0
    typeTimer = window.setInterval(() => {
      const w = WORDS[wi]!
      if (hold > 0) {
        hold--
        return
      }
      if (!del) {
        ci++
        typed.value = w.slice(0, ci)
        if (ci === w.length) {
          del = true
          hold = 16
        }
      } else {
        ci--
        typed.value = w.slice(0, ci)
        if (ci === 0) {
          del = false
          wi = (wi + 1) % WORDS.length
          hold = 5
        }
      }
    }, 52)
  } else {
    typed.value = WORDS[0]!
  }
}

onMounted(() => {
  loadRemember()
  startScene()
  if (auth.token) router.push({ name: 'home' })
})

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  if (typeTimer) clearInterval(typeTimer)
  cleanupResize?.()
})
</script>

<template>
  <div class="lg">
    <canvas ref="canvasEl" class="lg-canvas"></canvas>
    <div class="lg-veil"></div>
    <div class="lg-scan"></div>
    <div class="lg-hud lg-tl"><span class="mark">◍ SUPER AGENT</span><span class="sep">/</span><span>内网部署 · 单机多用户</span></div>
    <div class="lg-hud lg-bl"><span>本地 · 127.0.0.1</span><span class="sep">·</span><span>FastAPI + Vue3</span><span class="sep">·</span><span>会话 / 记忆 / 工作区按用户隔离</span></div>
    <div class="lg-hud lg-br" ref="statsEl">NODES — · LINK — · EVT —</div>
    <div class="lg-content">
      <div class="lg-brandbox">
        <div class="lg-eyebrow"><span class="dot"></span>AGENT LOOP · ONLINE</div>
        <h1 class="lg-word">Super <em>Agent</em></h1>
        <div class="lg-tag">内网通用任务 Agent — 通过工具系统获得能力，编码只是其中之一。仿 pi 架构，FastAPI + Vue3，多用户同机使用。</div>
        <div class="lg-type">&gt; <span>{{ typed }}</span><span class="cur">▌</span></div>
      </div>
      <form class="lg-card" @submit.prevent="submit">
        <h2>{{ mode === 'login' ? '登录' : '注册' }}</h2>
        <p class="sub">{{ mode === 'login' ? '使用内网账号进入工作台' : '创建内网账号（首次使用）' }}</p>
        <div class="lg-field">
          <label>用户名</label>
          <input v-model="username" type="text" placeholder="admin" autocomplete="username" required />
        </div>
        <div class="lg-field">
          <label>密码</label>
          <div class="lg-pwd-wrap">
            <input
              v-model="password"
              :type="showPwd ? 'text' : 'password'"
              placeholder="••••••••"
              autocomplete="current-password"
              required
            />
            <button type="button" class="lg-pwd-eye" :title="showPwd ? '隐藏密码' : '显示密码'" @click="showPwd = !showPwd">
              <svg v-if="showPwd" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
              <svg v-else viewBox="0 0 24 24" aria-hidden="true">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>
          </div>
        </div>
        <p v-if="error" class="lg-error login-error">{{ error }}</p>
        <button class="lg-submit" type="submit" :disabled="busy">{{ busy ? '提交中…' : mode === 'login' ? '登录' : '注册并进入' }}</button>
        <button class="login-switch lg-note" type="button" @click="mode = mode === 'login' ? 'register' : 'login'; error = ''">
          {{ mode === 'login' ? '没有账号？注册一个 →' : '已有账号？返回登录 →' }}
        </button>
        <div class="lg-note">token 存 localStorage；WS 连接以 query param 携带。</div>
      </form>
    </div>
  </div>
</template>

<style scoped>
/* 登录页固定深色场景（不随主题）——原型 .lg 系列 */
.lg{position:relative;height:100%;overflow:hidden;background:#0A0F15;color:#E8EEF2;--lg-a:#45C4D8}
.lg-canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.lg-veil{position:absolute;inset:0;pointer-events:none;background:
  radial-gradient(1100px 620px at 30% 42%,rgba(69,196,216,.11),transparent 62%),
  radial-gradient(900px 700px at 80% 80%,rgba(14,116,144,.14),transparent 65%),
  linear-gradient(180deg,rgba(10,15,21,.2),transparent 30%,transparent 70%,rgba(10,15,21,.55))}
.lg-scan{position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(255,255,255,.02) 0 1px,transparent 1px 3px)}
.lg-content{position:relative;z-index:2;height:100%;display:flex;align-items:center;justify-content:space-between;gap:48px;padding:56px 72px}
.lg-brandbox{max-width:600px}
.lg-eyebrow{display:inline-flex;align-items:center;gap:8px;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:11.5px;letter-spacing:.16em;color:var(--lg-a);border:1px solid rgba(69,196,216,.28);border-radius:999px;padding:5px 13px;background:rgba(69,196,216,.07)}
.lg-eyebrow .dot{width:6px;height:6px;border-radius:50%;background:var(--lg-a);animation:lgPulse 1.6s ease-in-out infinite}
@keyframes lgPulse{50%{box-shadow:0 0 0 5px rgba(69,196,216,.25)}}
.lg-word{margin:26px 0 0;font-size:clamp(46px,5.6vw,80px);font-weight:600;letter-spacing:-.025em;line-height:1.04;color:#F2F7FA}
.lg-word em{font-style:normal;background:linear-gradient(92deg,#45C4D8 0%,#A8E8F2 55%,#F2F7FA 100%);background-size:180% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:lgSheen 7s ease-in-out infinite alternate}
@keyframes lgSheen{from{background-position:0% 0}to{background-position:100% 0}}
.lg-tag{margin-top:20px;font-size:15px;color:#9FB2BE;max-width:46ch;line-height:1.8}
.lg-type{margin-top:16px;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:13.5px;color:#8ED8E6;min-height:20px}
.lg-type .cur{display:inline-block;margin-left:1px;color:var(--lg-a);animation:lgBlink 1.05s steps(1) infinite}
@keyframes lgBlink{50%{opacity:0}}
.lg-hud{position:absolute;z-index:2;display:flex;align-items:center;gap:10px;font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:11px;letter-spacing:.06em;color:#5E7B89;font-variant-numeric:tabular-nums}
.lg-hud .sep{color:#2C4350}
.lg-tl{top:22px;left:28px;color:#9FB2BE}
.lg-tl .mark{color:var(--lg-a)}
.lg-bl{left:28px;bottom:20px}
.lg-br{right:28px;bottom:20px}
.lg-card{flex:none;width:min(400px,100%);border:1px solid rgba(120,200,215,.18);border-radius:18px;background:rgba(13,20,27,.68);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);padding:30px 28px;box-shadow:0 24px 70px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.05)}
.lg-card h2{margin:0;font-size:20px;font-weight:600;color:#F2F7FA}
.lg-card .sub{margin:6px 0 24px;font-size:13px;color:#8CA1AD}
.lg-field{margin-bottom:15px}
.lg-field label{display:block;font-size:12px;font-weight:500;color:#9FB2BE;margin-bottom:6px}
.lg-field input{width:100%;border:1px solid rgba(120,200,215,.18);border-radius:10px;background:rgba(255,255,255,.045);padding:11px 13px;font-size:14px;color:#E8EEF2;outline:none;transition:border-color .15s,background .15s}
.lg-field input::placeholder{color:#5E7280}
.lg-field input:focus{border-color:var(--lg-a);background:rgba(69,196,216,.07)}
.lg-pwd-wrap{position:relative}
.lg-pwd-wrap input{padding-right:42px}
.lg-pwd-eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:30px;height:30px;padding:0;border:none;border-radius:8px;background:transparent;color:#5E7B89;cursor:pointer}
.lg-pwd-eye:hover{color:var(--lg-a);background:rgba(69,196,216,.08)}
.lg-pwd-eye svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.lg-error{margin:0 0 12px;font-size:12.5px;color:#F0716C}
.lg-submit{width:100%;padding:11px;margin-top:8px;font-size:14px;font-weight:600;border:none;border-radius:10px;background:linear-gradient(92deg,#0E7490,#45C4D8);color:#062A32;cursor:pointer}
.lg-submit:hover{filter:brightness(1.1)}
.lg-submit:disabled{opacity:.6;cursor:default}
.lg-note{margin-top:18px;font-size:11.5px;color:#5E7280;line-height:1.8}
@media (max-width:900px){.lg-brandbox{display:none}.lg-content{justify-content:center;padding:24px}.lg-bl{display:none}}
</style>
