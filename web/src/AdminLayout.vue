<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute } from 'vue-router'
import { api } from './api'

const connected = ref<boolean | null>(null)
const confirmRestart = ref(false)
const restartingAdb = ref(false)

async function restartAdb() {
  if (restartingAdb.value) return
  restartingAdb.value = true
  try {
    connected.value = (await api.restartAdb()).connected
    confirmRestart.value = false
    Message.success(connected.value ? 'ADB 已重启，手机已连接' : 'ADB 已重启，等待手机连接')
  } catch (cause) {
    Message.error((cause as Error).message)
  } finally {
    restartingAdb.value = false
    void refreshPhoneStatus()
  }
}

let timer: number | undefined
let checking = false

async function refreshPhoneStatus() {
  if (document.hidden || checking || restartingAdb.value) return
  checking = true
  try {
    const status = await api.senderStatus()
    if (!restartingAdb.value) connected.value = status.connected
  } catch {
    if (!restartingAdb.value) connected.value = null
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
      <nav aria-label="本地报告">
        <a href="/api/reader/trial-preview" target="_blank" rel="noopener">审判预览</a>
      </nav>
    </aside>
    <a-layout class="admin-main">
      <a-layout-header class="admin-header">
        <span>{{ tabs.find(item => item.path === route.path)?.label }}</span>
        <div class="header-actions">
        <a-tag :color="connected === true ? 'green' : connected === false ? 'orange' : 'gray'" role="status">
          {{ connected === true ? '手机已连接' : connected === false ? '手机不可用' : '手机状态待确认' }}
        </a-tag>
        <a-button size="small" :loading="restartingAdb" @click="confirmRestart = true">重启 ADB</a-button>
        </div>
      </a-layout-header>
      <a-layout-content class="admin-content"><RouterView /></a-layout-content>
    </a-layout>
    <a-modal :visible="confirmRestart" title="重启 ADB 服务" ok-text="确认重启"
      :ok-button-props="{ status: 'danger' }" :ok-loading="restartingAdb"
      :closable="!restartingAdb" :mask-closable="false" :esc-to-close="!restartingAdb"
      @ok="restartAdb" @cancel="!restartingAdb && (confirmRestart = false)">
      重启会暂时断开手机调试连接，无线调试可能需要重新连接。
    </a-modal>
  </a-layout>
</template>

<style scoped>
.admin-layout { min-height: 100vh; flex-direction: row; background: var(--console-canvas); }
.sidebar { position: sticky; top: 0; height: 100vh; flex: 0 0 192px; background: #162235; padding: 24px 14px; overflow: auto; }
.brand { display: block; color: white; font-size: 19px; font-weight: 750; padding: 0 12px; margin-bottom: 26px; }
nav { display: grid; gap: 5px; }
nav a { display: block; padding: 9px 12px; border-radius: 7px; color: #c2ccdb; }
.brand:hover, nav a:hover, nav a:focus-visible { text-decoration: none; }
nav a:hover { background: #22334b; color: white; }
nav a.router-link-exact-active { background: #304669; color: white; font-weight: 600; }
.admin-main,.admin-content { min-width: 0; }
.admin-header { display: flex; align-items: center; justify-content: space-between; min-height: 56px; padding: 0 clamp(16px, 3vw, 40px); background: white; border-bottom: 1px solid var(--console-border); font-weight: 600; }
.header-actions { display: flex; align-items: center; gap: 8px; }
@media (max-width: 760px) {
  .admin-layout { flex-direction: column; }
  .sidebar { position: static; height: auto; flex: none; padding: 10px 14px; }
  .brand { margin-bottom: 8px; }
  nav { display: flex; overflow-x: auto; }
  nav a { white-space: nowrap; }
  .admin-header { min-height: 44px; }
}
</style>
