<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'

const connected = ref<boolean | null>(null)
let timer: number | undefined
let checking = false

async function refreshPhoneStatus() {
  if (document.hidden || checking) return
  checking = true
  try {
    connected.value = (await api.senderStatus()).connected
  } catch {
    connected.value = null
  } finally {
    checking = false
  }
}

onMounted(() => {
  void refreshPhoneStatus()
  timer = window.setInterval(refreshPhoneStatus, 5000)
  document.addEventListener('visibilitychange', refreshPhoneStatus)
})
onUnmounted(() => {
  window.clearInterval(timer)
  document.removeEventListener('visibilitychange', refreshPhoneStatus)
})

const route = useRoute()

const tabs = [
  { path: '/sender', label: 'Sender' },
  { path: '/sender-history', label: 'Sender 日志' },
  { path: '/wireless-debug', label: '无线调试' },
  { path: '/setup', label: '微信初始化' },
  { path: '/history', label: '聊天记录' },
  { path: '/rules', label: '全局配置' },
  { path: '/group', label: '群配置' },
  { path: '/plugins', label: '插件管理' },
]

</script>

<template>
  <a-layout class="admin-layout">
    <aside class="sidebar">
      <RouterLink to="/sender" class="brand">wechat-bot</RouterLink>
      <nav aria-label="后台导航">
        <RouterLink v-for="item in tabs" :key="item.path" :to="item.path">{{ item.label }}</RouterLink>
      </nav>
    </aside>
    <a-layout class="admin-main">
      <a-layout-header class="admin-header">
        <span>{{ tabs.find(item => item.path === route.path)?.label }}</span>
        <a-tag :color="connected === true ? 'green' : connected === false ? 'orange' : 'gray'" role="status">
          {{ connected === true ? '手机已连接' : connected === false ? '手机不可用' : '手机状态待确认' }}
        </a-tag>
      </a-layout-header>
      <a-layout-content class="admin-content"><RouterView /></a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.admin-layout { min-height: 100vh; flex-direction: row; background: var(--console-canvas); }
.sidebar { position: sticky; top: 0; height: 100vh; flex: 0 0 192px; background: #162235; padding: 24px 14px; overflow: auto; }
.brand { display: block; color: white; font-size: 19px; font-weight: 750; padding: 0 12px; margin-bottom: 26px; }
nav { display: grid; gap: 5px; }
nav a { display: block; padding: 9px 12px; border-radius: 7px; color: #c2ccdb; }
nav a:hover { background: #22334b; color: white; }
nav a.router-link-exact-active { background: #304669; color: white; font-weight: 600; }
.admin-main,.admin-content { min-width: 0; }
.admin-header { display: flex; align-items: center; justify-content: space-between; min-height: 56px; padding: 0 clamp(16px, 3vw, 40px); background: white; border-bottom: 1px solid var(--console-border); font-weight: 600; }
@media (max-width: 760px) {
  .admin-layout { flex-direction: column; }
  .sidebar { position: static; height: auto; flex: none; padding: 10px 14px; }
  .brand { margin-bottom: 8px; }
  nav { display: flex; overflow-x: auto; }
  nav a { white-space: nowrap; }
  .admin-header { min-height: 44px; }
}
</style>