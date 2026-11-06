<script setup lang="ts">
import { ref } from 'vue'
import { api } from './api'

const query = ref('')
const automatic = ref(true)
const port = ref(5032)
const token = ref('')
const groups = ref<{ id: string; name: string }[]>([])
const selected = ref('')
const busy = ref(false)
const notice = ref('')
const error = ref('')

async function probe(checkMessages = false) {
  busy.value = true
  notice.value = ''
  error.value = ''
  try {
    const result = await api.probeReader({
      query: query.value.trim(),
      ...(automatic.value ? {} : { port: port.value, token: token.value }),
      ...(checkMessages ? { session_id: selected.value } : {}),
    })
    groups.value = result.groups
    if (!groups.value.some((group) => group.id === selected.value)) selected.value = ''
    notice.value = result.message_readable
      ? '已验证所选群的消息读取。'
      : `连接成功，找到 ${result.groups.length} 个群。${result.has_more ? '可输入群名缩小范围。' : ''}`
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="card">
    <h2>电脑端微信读取</h2>
    <p>默认使用本地构建的密语 CLI 读取微信数据库。也可以连接已启动的兼容本地代理。</p>
    <label>
      <select v-model="automatic">
        <option :value="true">本地源码 CLI（自动读取本机设置）</option>
        <option :value="false">手动填写代理端口和令牌</option>
      </select>
    </label>
    <template v-if="!automatic">
      <label>本机端口 <input v-model.number="port" type="number" min="1" max="65535" /></label>
      <label>访问令牌 <input v-model="token" type="password" autocomplete="off" /></label>
    </template>
    <form @submit.prevent="probe()">
      <label>群名 <input v-model="query" placeholder="输入群名以缩小范围" /></label>
      <button :disabled="busy">测试连接并搜索群聊</button>
    </form>
    <template v-if="groups.length">
      <label>验证消息读取
        <select v-model="selected">
          <option value="">请选择群</option>
          <option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }} · {{ group.id }}</option>
        </select>
      </label>
      <button :disabled="busy || !selected" @click="probe(true)">验证所选群</button>
    </template>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message ok" role="status">{{ notice }}</p>
  </section>
</template>
