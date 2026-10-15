<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import ConnectionBadge from '@/components/ConnectionBadge.vue'
import HealthBadge from '@/components/HealthBadge.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const VIDEO_SRC = `${import.meta.env.BASE_URL}hero-loop.mp4`

/* 视频画面上的热区（仿 Dataphin welcome 区的透明可点击模块）。
   坐标为百分比、相对视频容器；对着画面微调这组常量即可，不用动模板。 */
interface Hotspot {
  key: string
  label: string
  to: string
  top: number
  left: number
  width: number
  height: number
  primary?: boolean
}

const HOTSPOTS: Hotspot[] = [
  { key: 'chat', label: '进入会话', to: '/', top: 16, left: 18, width: 34, height: 20, primary: true },
  { key: 'sessions', label: '会话管理', to: '/sessions', top: 42, left: 44, width: 30, height: 17 },
  { key: 'settings', label: '设置', to: '/settings', top: 66, left: 20, width: 26, height: 15 },
]

/* 视频内嵌英文标签的覆盖替换（坐标用 .scratch/hero-video 的帧测量标定）。
   left/top/width/height 为相对视频容器的百分比，rotate 为各平台倾角。 */
interface Relabel {
  key: string
  text: string
  left: number
  top: number
  width: number
  height: number
  rotate: number
}

const RELABELS: Relabel[] = [
  {
    key: 'asset',
    text: 'Session Memory Assets',
    left: 56.14,
    top: 41.42,
    width: 23.5,
    height: 3.4,
    rotate: 26.4,
  },
]

const videoFailed = ref(false)

function onVideoError(): void {
  videoFailed.value = true
}

/* 右侧引导网格（仿 Dataphin guid-list-grid：分类列 + 入口卡） */
interface GuideCard {
  label: string
  desc: string
  to?: string
  logout?: boolean
  info?: boolean
}
interface GuideColumn {
  title: string
  cards: GuideCard[]
}

const GUIDE: GuideColumn[] = [
  {
    title: '会话',
    cards: [
      { label: '开始新会话', desc: '进入会话界面', to: '/' },
      { label: '会话管理', desc: '查看与管理全部会话', to: '/sessions' },
    ],
  },
  {
    title: '配置',
    cards: [
      { label: '连接与模型', desc: 'MCP 与模型入口设置', to: '/settings' },
      { label: '运行状态', desc: '健康检查与连接情况', to: '/settings' },
    ],
  },
  {
    title: '账户',
    cards: [
      { label: auth.username ?? '未登录', desc: '当前登录账户', info: true },
      { label: '退出登录', desc: '清除凭证并返回登录页', logout: true },
    ],
  },
]

function openCard(card: GuideCard): void {
  if (card.info) return
  if (card.logout) {
    logout()
    return
  }
  if (card.to) router.push(card.to)
}

function logout(): void {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="dp-home">
    <nav class="dp-nav">
      <span class="dp-logo">Super Agent</span>
      <div class="dp-menu">
        <RouterLink to="/">会话</RouterLink>
        <RouterLink to="/sessions">会话管理</RouterLink>
        <RouterLink to="/settings">设置</RouterLink>
      </div>
      <div class="dp-nav-right">
        <HealthBadge />
        <ConnectionBadge v-if="auth.token" />
        <span class="dp-user">{{ auth.username ?? '未登录' }}</span>
        <button type="button" class="dp-logout" @click="logout">退出登录</button>
      </div>
    </nav>

    <div class="dp-content">
      <section class="dp-welcome">
        <video
          v-if="!videoFailed"
          class="dp-video"
          :src="VIDEO_SRC"
          autoplay
          loop
          muted
          playsinline
          preload="auto"
          @error="onVideoError"
        ></video>
        <div v-else class="dp-fallback" aria-hidden="true"><i></i><i></i><i></i></div>
        <div v-if="!videoFailed" aria-hidden="true">
          <div
            v-for="r in RELABELS"
            :key="r.key"
            class="dp-relabel"
            :style="{ left: r.left + '%', top: r.top + '%', width: r.width + '%', height: r.height + '%', '--rot': r.rotate + 'deg' }"
          >
            <i class="dp-relabel-patch"></i>
            <span class="dp-relabel-text">{{ r.text }}</span>
          </div>
        </div>
        <RouterLink
          v-for="h in HOTSPOTS"
          :key="h.key"
          class="dp-hotspot"
          :class="{ 'is-primary': h.primary }"
          :to="h.to"
          :style="{ top: h.top + '%', left: h.left + '%', width: h.width + '%', height: h.height + '%' }"
        >
          <span class="dp-hotspot-label">{{ h.label }}</span>
        </RouterLink>
      </section>

      <aside class="dp-guide">
        <div v-for="col in GUIDE" :key="col.title" class="dp-guide-col">
          <div class="dp-guide-label">
            <span class="dp-guide-title">{{ col.title }}</span>
            <svg viewBox="0 0 6 100" preserveAspectRatio="none" aria-hidden="true">
              <path
                d="M 3,0 L3,100"
                fill="none"
                stroke="currentColor"
                stroke-width="1"
                stroke-dasharray="3"
                opacity="0.2"
                vector-effect="non-scaling-stroke"
              />
            </svg>
          </div>
          <button
            v-for="card in col.cards"
            :key="card.label"
            type="button"
            class="dp-card"
            :class="{ 'is-info': card.info }"
            @click="openCard(card)"
          >
            <span class="dp-card-title">{{ card.label }}</span>
            <span class="dp-card-desc">{{ card.desc }}</span>
          </button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.dp-home {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* ── 顶部导航（仿 Dataphin left-area / middle-area） ── */
.dp-nav {
  flex: none;
  display: flex;
  align-items: center;
  gap: 16px;
  height: 52px;
  padding: 0 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--line2);
}

.dp-logo {
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--ink);
}

.dp-menu {
  flex: 1;
  display: flex;
  gap: 4px;
  overflow: hidden;
}

.dp-menu a {
  padding: 6px 14px;
  border-radius: 8px;
  color: var(--ink2);
  font-size: 14px;
  text-decoration: none;
  white-space: nowrap;
}

.dp-menu a:hover {
  background: var(--surface2);
  color: var(--ink);
}

.dp-menu a.router-link-active {
  color: var(--accent);
  font-weight: 500;
}

.dp-nav-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dp-user {
  color: var(--ink2);
  font-size: 13px;
  white-space: nowrap;
}

.dp-logout {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink2);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
}

.dp-logout:hover {
  color: var(--error);
  border-color: var(--error);
}

/* ── 主区：左视频热区 + 右引导网格 ── */
.dp-content {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 28px;
  padding: 22px 26px;
}

.dp-welcome {
  position: relative;
  flex: 0 1 auto;
  height: 100%;
  aspect-ratio: 720 / 824; /* 视频原始尺寸，保证热区百分比与画面对位 */
  max-width: 100%;
  border-radius: 12px;
  overflow: hidden;
  background: var(--surface2);
  box-shadow: var(--shadow);
  container-type: inline-size; /* 供 .dp-relabel-text 用 cqw 随面板缩放 */
}

/* 视频内嵌标签的覆盖替换：胶囊形色块 + 新文字，pointer-events 穿透不挡热区 */
.dp-relabel {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  transform: translate(-50%, -50%) rotate(var(--rot));
  pointer-events: none;
}

.dp-relabel-patch {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, #848bc3, #b3b6e4);
  border-radius: 999px;
}

.dp-relabel-text {
  position: relative;
  font-size: 2.92cqw;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #dddef6;
  text-shadow: 0 1px 1px rgb(60 62 100 / 0.5);
  white-space: nowrap;
  transform: scaleX(0.96);
}

.dp-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 视频缺失时的 CSS 动效兜底 */
.dp-fallback {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: linear-gradient(155deg, var(--accent-soft) 0%, var(--surface2) 60%, var(--surface) 100%);
}

.dp-fallback i {
  position: absolute;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.14;
  filter: blur(28px);
  animation: dp-float 7s ease-in-out infinite alternate;
}

.dp-fallback i:nth-child(1) {
  width: 46%;
  height: 30%;
  top: 8%;
  left: 6%;
}

.dp-fallback i:nth-child(2) {
  width: 38%;
  height: 26%;
  top: 44%;
  left: 40%;
  animation-delay: -2.5s;
}

.dp-fallback i:nth-child(3) {
  width: 42%;
  height: 28%;
  top: 70%;
  left: 12%;
  animation-delay: -5s;
}

@keyframes dp-float {
  from {
    transform: translateY(-5%) scale(1);
  }
  to {
    transform: translateY(6%) scale(1.12);
  }
}

@media (prefers-reduced-motion: reduce) {
  .dp-fallback i {
    animation: none;
  }
}

/* 热区：默认透明，hover 时 accent 描边高亮；主热区标签常显 */
.dp-hotspot {
  position: absolute;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 8px;
  border: 1px dashed transparent;
  border-radius: 10px;
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s;
}

.dp-hotspot:hover {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.dp-hotspot-label {
  font-size: 12px;
  color: var(--accent);
  background: var(--surface);
  border: 1px solid var(--accent);
  padding: 2px 10px;
  border-radius: 999px;
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}

.dp-hotspot:hover .dp-hotspot-label,
.dp-hotspot.is-primary .dp-hotspot-label {
  opacity: 1;
}

/* ── 右侧引导网格 ── */
.dp-guide {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  align-content: start;
}

.dp-guide-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.dp-guide-label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--ink);
}

.dp-guide-title {
  font-weight: 600;
  font-size: 14px;
}

.dp-guide-label svg {
  width: 6px;
  height: 26px;
}

.dp-card {
  text-align: left;
  font: inherit;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--line2);
  border-radius: 10px;
  cursor: pointer;
  transition:
    border-color 0.15s,
    box-shadow 0.15s,
    transform 0.15s;
}

.dp-card:hover:not(.is-info) {
  border-color: var(--accent);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}

.dp-card.is-info {
  cursor: default;
}

.dp-card-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dp-card-desc {
  font-size: 12px;
  color: var(--ink3);
}

/* ── 窄屏：上下堆叠 ── */
@media (max-width: 920px) {
  .dp-content {
    flex-direction: column;
    overflow-y: auto;
  }

  .dp-welcome {
    width: 100%;
    height: auto;
  }

  .dp-guide {
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  }

  .dp-user {
    display: none;
  }
}
</style>
