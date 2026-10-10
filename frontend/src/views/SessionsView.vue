<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchSessions, type SessionSummary } from '@/api/rest'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()

const sessions = ref<SessionSummary[]>([])
const query = ref('')
const filter = ref<'all' | 'running' | 'idle'>('all')
const loadError = ref('')

const inputEl = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  try {
    const token = useAuthStore().token
    if (token) sessions.value = await fetchSessions(token)
  } catch {
    loadError.value = '会话列表加载失败'
  }
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return sessions.value.filter((s) => {
    if (filter.value === 'running' && !s.running) return false
    if (filter.value === 'idle' && s.running) return false
    return !q || s.title.toLowerCase().includes(q)
  })
})

function dayLabel(updatedAt: number): string {
  const d = new Date(updatedAt * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function tokensLabel(total: number): string {
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total)
}

function sessTitle(s: SessionSummary): string {
  return s.title || `会话 · ${s.msg_count} 条消息`
}

async function openSession(s: SessionSummary): Promise<void> {
  await chat.loadSessions() // 保证 chat.sessions 是最新
  chat.switchSession(s.id)
  router.push('/')
}

async function removeSession(s: SessionSummary): Promise<void> {
  if (!window.confirm(`删除会话「${sessTitle(s)}」？其全部历史将不可恢复。`)) return
  await chat.deleteSession(s.id)
  sessions.value = sessions.value.filter((x) => x.id !== s.id)
}
</script>

<template>
  <div class="sn">
    <div class="sn-top">
      <span class="sn-title">Sessions</span>
      <label class="sn-search">
        <span class="sn-ic">⌕</span>
        <input ref="inputEl" v-model="query" placeholder="搜索会话…" />
      </label>
      <div class="sn-fchips">
        <button class="sn-fchip" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部</button>
        <button class="sn-fchip" :class="{ on: filter === 'running' }" @click="filter = 'running'">运行中</button>
        <button class="sn-fchip" :class="{ on: filter === 'idle' }" @click="filter = 'idle'">空闲</button>
      </div>
      <button class="btn btn-primary" @click="router.push('/')">＋ 新会话</button>
    </div>
    <div class="sn-body">
      <div class="sn-wrap">
        <p v-if="loadError" class="sn-empty">{{ loadError }}</p>
        <p v-else class="sn-count tmeta">{{ filtered.length }} / {{ sessions.length }} sessions</p>
        <div v-if="!loadError" class="sn-tablebox">
          <div class="sn-scrollbox">
            <table class="sn-table">
              <thead>
                <tr><th>会话</th><th>状态</th><th>最近更新</th><th>消息</th><th>Tokens</th><th></th></tr>
              </thead>
              <tbody>
                <tr
                  v-for="s in filtered"
                  :key="s.id"
                  :class="{ run: s.running }"
                  @click="openSession(s)"
                >
                  <td><div class="sn-tt">{{ sessTitle(s) }}</div></td>
                  <td>
                    <span class="sn-st">
                      <span class="sn-dot" :class="s.running ? 'run' : 'idle'"></span>
                      {{ s.running ? '运行中' : '空闲' }}
                    </span>
                  </td>
                  <td class="sn-td-dim">{{ dayLabel(s.updated_at) }}</td>
                  <td class="sn-td-dim mono">{{ s.msg_count }}</td>
                  <td class="sn-td-dim mono">{{ tokensLabel(s.total_tokens) }}</td>
                  <td>
                    <div class="sn-actions">
                      <button class="sn-act" title="打开" @click.stop="openSession(s)">↗</button>
                      <button class="sn-act" title="删除" @click.stop="removeSession(s)">✕</button>
                    </div>
                  </td>
                </tr>
                <tr v-if="filtered.length === 0">
                  <td colspan="6"><div class="sn-empty">没有匹配的会话 — 换个关键词或过滤器</div></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <p class="foot-note">↗ 打开进入工作台；✕ 删除会话（真删除，JSONL 一并移除，无归档态）。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sn{height:100%;display:flex;flex-direction:column}
.sn-top{flex:none;display:flex;align-items:center;gap:12px;padding:16px 26px;border-bottom:1px solid var(--line2);flex-wrap:wrap}
.sn-title{font-size:16px;font-weight:600;margin-right:auto}
.sn-search{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:7px 12px;min-width:220px}
.sn-search:focus-within{border-color:var(--accent)}
.sn-search input{border:none;background:transparent;outline:none;font-size:13px;width:170px}
.sn-search input::placeholder{color:var(--ink3)}
.sn-ic{color:var(--ink3);font-size:13px}
.sn-fchips{display:flex;gap:6px}
.sn-fchip{border:1px solid var(--line);border-radius:999px;padding:4px 13px;font-size:12px;color:var(--ink2);background:var(--surface)}
.sn-fchip:hover{border-color:var(--ink3)}
.sn-fchip.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:500}
.sn-body{flex:1;overflow-y:auto;min-height:0}
.sn-wrap{max-width:980px;margin:0 auto;padding:20px 26px 80px}
.sn-count{font-size:12px;color:var(--ink3);margin:0 0 10px}
.sn-tablebox{border:1px solid var(--line2);border-radius:12px;background:var(--surface);overflow:hidden}
.sn-scrollbox{overflow-x:auto}
.sn-table{width:100%;border-collapse:collapse;font-size:13px;min-width:640px}
.sn-table th{text-align:left;font-size:11px;letter-spacing:.08em;color:var(--ink3);font-weight:500;padding:10px 16px;border-bottom:1px solid var(--line2);background:var(--surface2);white-space:nowrap}
.sn-table td{padding:12px 16px;border-bottom:1px solid var(--line2);vertical-align:middle}
.sn-table tr:last-child td{border-bottom:none}
.sn-table tbody tr{cursor:pointer}
.sn-table tbody tr:hover td{background:var(--surface2)}
.sn-table tr.run td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.sn-st{display:inline-flex;align-items:center;gap:7px;font-size:12px;white-space:nowrap}
.sn-dot{width:7px;height:7px;border-radius:50%;flex:none}
.sn-dot.run{background:var(--accent);animation:pulse 1.6s infinite}
.sn-dot.idle{background:var(--ink3)}
.sn-tt{font-weight:500;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sn-td-dim{color:var(--ink3);font-size:12px;white-space:nowrap}
.sn-actions{display:flex;gap:4px;justify-content:flex-end}
.sn-act{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;color:var(--ink3);font-size:13px}
.sn-act:hover{background:var(--surface2);color:var(--ink)}
.sn-empty{padding:44px 0;text-align:center;color:var(--ink3);font-size:13px}
.foot-note{margin-top:14px;font-size:11.5px;color:var(--ink3);line-height:1.8}
</style>
