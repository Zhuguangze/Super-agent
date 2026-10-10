<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { patchSession, type SessionSummary } from '@/api/rest'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
const auth = useAuthStore()
const router = useRouter()

const menuForId = ref<string | null>(null)
const renamingId = ref<string | null>(null)
const renameDraft = ref('')
const userMenuOpen = ref(false)

onMounted(() => {
  void chat.loadSessions()
  window.addEventListener('click', onGlobalClick)
})

onBeforeUnmount(() => {
  window.removeEventListener('click', onGlobalClick)
})

function onGlobalClick(ev: MouseEvent): void {
  const target = ev.target as HTMLElement
  if (!target.closest('.wk-sess') && !target.closest('.sess-menu')) {
    menuForId.value = null
  }
  if (!target.closest('.wk-userbtn') && !target.closest('.wk-usermenu')) {
    userMenuOpen.value = false
  }
}

const initial = computed(() => (auth.username || 'U').charAt(0).toUpperCase())
const sorted = computed(() => chat.sessions)

function dayLabel(updatedAt: number): string {
  const date = new Date(updatedAt * 1000)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function sessTitle(s: { title: string; msg_count: number }): string {
  return s.title || `会话 · ${s.msg_count} 条消息`
}

function toggleMenu(s: SessionSummary): void {
  menuForId.value = menuForId.value === s.id ? null : s.id
  renamingId.value = null
}

async function togglePin(s: SessionSummary): Promise<void> {
  menuForId.value = null
  try {
    await patchSession(useAuthStore().token!, s.id, { pinned: !s.pinned })
    await chat.loadSessions()
  } catch {
    /* 刷新失败保留现状 */
  }
}

function startRename(s: SessionSummary): void {
  menuForId.value = null
  renamingId.value = s.id
  renameDraft.value = s.title
}

async function commitRename(s: SessionSummary): Promise<void> {
  const title = renameDraft.value.trim()
  renamingId.value = null
  if (!title || title === s.title) return
  try {
    await patchSession(useAuthStore().token!, s.id, { title })
    await chat.loadSessions()
  } catch {
    /* 同上 */
  }
}

async function removeSession(s: SessionSummary): Promise<void> {
  menuForId.value = null
  if (!window.confirm(`删除会话「${sessTitle(s)}」？其全部历史将不可恢复。`)) return
  await chat.deleteSession(s.id)
}

function logout(): void {
  userMenuOpen.value = false
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <aside class="wk-side">
    <div class="wk-brand">Super<em> Agent</em></div>
    <button class="btn wk-new" :disabled="chat.running" @click="chat.newSession">＋ 新会话</button>
    <div class="wk-sessions">
      <div
        v-for="s in sorted"
        :key="s.id"
        class="wk-sesswrap"
      >
        <button
          class="wk-sess"
          :class="{ active: s.id === chat.currentSessionId }"
          @click="chat.switchSession(s.id); menuForId = null"
        >
          <span class="t">
            <span v-if="s.pinned" class="pin" title="已置顶">📌</span>{{ sessTitle(s) }}
          </span>
          <span class="s">
            <i v-if="s.running" class="run-dot"></i>
            {{ dayLabel(s.updated_at) }} · {{ s.msg_count }} 条
          </span>
        </button>
        <button
          class="sess-more"
          title="会话操作"
          @click.stop="toggleMenu(s)"
        >⋮</button>

        <div v-if="menuForId === s.id" class="sess-menu" @click.stop>
          <button class="wk-mi" type="button" @click="togglePin(s)">
            <span class="gl">📌</span><span class="nm">{{ s.pinned ? '取消置顶' : '置顶' }}</span>
            <span class="sp"></span><span class="kbd">P</span>
          </button>
          <button class="wk-mi" type="button" @click="startRename(s)">
            <span class="gl">✎</span><span class="nm">重命名</span>
            <span class="sp"></span><span class="kbd">R</span>
          </button>
          <div class="wk-msep"></div>
          <button class="wk-mi danger" type="button" @click="removeSession(s)">
            <span class="gl">🗑</span><span class="nm">删除</span>
            <span class="sp"></span><span class="kbd">D</span>
          </button>
        </div>

        <input
          v-if="renamingId === s.id"
          v-model="renameDraft"
          class="rename-input"
          type="text"
          autofocus
          @keydown.enter.prevent="commitRename(s)"
          @keydown.esc="renamingId = null"
          @blur="commitRename(s)"
          @click.stop
        />
      </div>
    </div>
    <RouterLink class="wk-all" to="/sessions"><span>全部会话</span><span>→</span></RouterLink>
    <div class="wk-userzone">
      <div v-if="userMenuOpen" class="wk-usermenu">
        <div class="um-model tmeta">{{ chat.currentModel }}</div>
        <RouterLink class="wk-mi" to="/settings" @click="userMenuOpen = false">
          <span class="gl">⚙</span><span class="nm">设置</span>
        </RouterLink>
        <RouterLink class="wk-mi" to="/sessions" @click="userMenuOpen = false">
          <span class="gl">☰</span><span class="nm">全部会话</span>
        </RouterLink>
        <div class="wk-msep"></div>
        <button class="wk-mi" type="button" @click="logout">
          <span class="gl">↪</span><span class="nm">退出登录</span>
        </button>
      </div>
      <button class="wk-userbtn" type="button" @click.stop="userMenuOpen = !userMenuOpen">
        <span class="avatar">{{ initial }}</span>
        <span class="uname-row">
          <span class="uname">{{ auth.username || 'user' }}</span>
          <span class="usep">·</span>
          <span class="uplan">本地</span>
        </span>
        <span class="sp"></span>
        <span class="chev" :class="{ open: userMenuOpen }">⌄</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.wk-side{height:100%;flex:none;width:264px;background:var(--ground);border-right:1px solid var(--line2);display:flex;flex-direction:column;padding:16px 12px 12px;position:relative}
.wk-brand{font-weight:600;font-size:14px;letter-spacing:.02em;padding:2px 10px 12px}
.wk-brand em{font-style:normal;color:var(--accent)}
.wk-new{justify-content:center;margin-bottom:14px}
.wk-sessions{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:2px}
.wk-sesswrap{position:relative}
.wk-sess{display:flex;flex-direction:column;gap:2px;padding:8px 26px 8px 10px;border-radius:8px;text-align:left;width:100%}
.wk-sess:hover{background:var(--surface2)}
.wk-sess.active{background:var(--accent-soft)}
.wk-sess .t{font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wk-sess.active .t{color:var(--accent)}
.wk-sess .s{font-size:11.5px;color:var(--ink3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:5px}
.run-dot{width:7px;height:7px;border-radius:50%;background:var(--accent);animation:pulse 1.6s infinite;flex:none}
.pin{margin-right:3px;font-size:10px}
.sess-more{position:absolute;top:6px;right:4px;width:22px;height:22px;border-radius:6px;display:grid;place-items:center;color:var(--ink3);font-size:13px;opacity:0;transition:opacity .12s}
.wk-sesswrap:hover .sess-more{opacity:1}
.sess-more:hover{background:var(--surface);color:var(--ink)}
.sess-menu{position:absolute;top:30px;right:4px;min-width:170px;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:5px;z-index:90}
.wk-mi{display:flex;align-items:center;gap:9px;width:100%;text-align:left;padding:7px 10px;border-radius:8px;font-size:12.5px;color:var(--ink)}
.wk-mi:hover{background:var(--surface2)}
.wk-mi.danger{color:var(--error)}
.wk-mi .gl{width:16px;text-align:center;flex:none;font-size:12px}
.wk-mi .sp{flex:1}
.kbd{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:10px;color:var(--ink3);border:1px solid var(--line);border-radius:4px;padding:0 4px}
.wk-msep{height:1px;background:var(--line2);margin:4px 6px}
.rename-input{width:100%;margin:2px 0;padding:6px 10px;border:1px solid var(--accent);border-radius:8px;background:var(--surface);font:inherit;font-size:13px;outline:none}
.wk-all{display:flex;align-items:center;justify-content:space-between;padding:9px 10px;margin-top:6px;border-top:1px solid var(--line2);font-size:12.5px;color:var(--ink2);text-align:left;text-decoration:none}
.wk-all:hover{color:var(--ink)}
.wk-userzone{position:relative;margin-top:8px;border-top:1px solid var(--line2);padding-top:6px}
.wk-userbtn{display:flex;align-items:center;gap:8px;width:100%;padding:6px 8px;border-radius:10px;text-align:left;transition:background .15s}
.wk-userbtn:hover{background:var(--surface2)}
.avatar{width:28px;height:28px;border-radius:50%;background:var(--ink);color:var(--ground);display:grid;place-items:center;font-size:13px;font-weight:600;flex:none}
.uname-row{display:flex;align-items:baseline;gap:5px;min-width:0}
.uname{font-size:13px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.usep{color:var(--ink3);font-size:12px}
.uplan{font-size:12px;color:var(--ink2);white-space:nowrap}
.sp{flex:1}
.chev{color:var(--ink3);font-size:10px;transition:transform .15s;flex:none}
.chev.open{transform:rotate(180deg)}
.wk-usermenu{position:absolute;bottom:calc(100% + 6px);left:0;right:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:5px;z-index:90}
.um-model{padding:5px 10px 7px;border-bottom:1px solid var(--line2);margin-bottom:4px}
.wk-usermenu .wk-mi{text-decoration:none}
</style>
