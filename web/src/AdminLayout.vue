<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const tabs = [
  { path: '/', label: '发送控制台' },
  { path: '/setup', label: '微信初始化' },
  { path: '/rules', label: '规则与任务' },
]

function navigate(path: string | number) {
  if (path !== route.path) void router.push(String(path))
}
</script>

<template>
  <a-layout class="admin-layout">
    <a-layout-header class="admin-header">
      <div class="admin-brand">
        <span class="admin-mark" aria-hidden="true">W</span>
        <div><strong>wechat-bot</strong><small>本地管理后台</small></div>
      </div>
      <a-tag color="arcoblue">本机工作区</a-tag>
    </a-layout-header>
    <nav aria-label="后台导航" class="admin-navigation">
      <a-tabs :active-key="route.path" type="line" @change="navigate">
        <a-tab-pane v-for="tab in tabs" :key="tab.path" :title="tab.label" />
      </a-tabs>
    </nav>
    <a-layout-content>
      <RouterView />
    </a-layout-content>
  </a-layout>
</template>

<style scoped>
.admin-layout { min-height: 100vh; background: var(--color-fill-1); }
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 68px;
  padding: 12px clamp(16px, 5vw, 64px);
  background: var(--color-bg-2);
  border-bottom: 1px solid var(--color-border-2);
}
.admin-brand { display: flex; align-items: center; gap: 12px; }
.admin-brand strong, .admin-brand small { display: block; }
.admin-brand small { color: var(--color-text-3); }
.admin-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  background: rgb(var(--primary-6));
  color: white;
  font-weight: 700;
}
.admin-navigation { padding: 0 clamp(8px, 4vw, 56px); background: var(--color-bg-2); }
.admin-navigation :deep(.arco-tabs-content) { display: none; }
</style>
