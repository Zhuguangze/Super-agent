import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import { hasStoredToken } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: ChatView,
    },
    {
      path: '/sessions',
      name: 'sessions',
      component: () => import('../views/SessionsView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
    },
  ],
})

router.beforeEach((to) => {
  if (to.name !== 'login' && !hasStoredToken()) {
    return { name: 'login' }
  }
  return true
})

export default router
