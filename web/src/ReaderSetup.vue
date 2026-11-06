<script setup lang="ts">
import { ref } from 'vue'
import { api } from './api'
import ReaderRules from './ReaderRules.vue'

const query = ref('')
const mode = ref('automatic')
const databasePath = ref('')
const account = ref('')
const databaseKey = ref('')
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
      ...(mode.value === 'proxy' ? { port: port.value, token: token.value } : {}),
      ...(mode.value === 'manual' ? {
        database_path: databasePath.value.trim(),
        account: account.value.trim(),
        database_key: databaseKey.value.trim(),
      } : {}),
      ...(checkMessages ? { session_id: selected.value } : {}),
    })
    groups.value = result.groups
    if (!groups.value.some((group) => group.id === selected.value)) selected.value = ''
    notice.value = result.message_readable
      ? result.has_messages ? '已验证所选群的消息读取。' : '消息库查询成功，但所选群目前没有本地消息记录。'
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
    <p>默认使用 SQLCipher 只读查询微信数据库，并自动读取本机账号配置。也可以连接已启动的兼容本地代理。</p>
    <label>
      <select v-model="mode">
        <option value="automatic">SQLCipher（自动读取本机设置）</option>
        <option value="manual">SQLCipher（手动验证）</option>
        <option value="proxy">手动填写代理端口和令牌</option>
      </select>
    </label>
    <template v-if="mode === 'proxy'">
      <label>本机端口 <input v-model.number="port" type="number" min="1" max="65535" /></label>
      <label>访问令牌 <input v-model="token" type="password" autocomplete="off" /></label>
    </template>
    <template v-if="mode === 'manual'">
      <label>数据库目录 <input v-model="databasePath" placeholder="账号目录或 db_storage" /></label>
      <label>微信账号 ID <input v-model="account" /></label>
      <label>数据库密钥 <input v-model="databaseKey" type="password" autocomplete="off" /></label>
      <p>这些字段只用于本次验证。后台监听使用本机设置或 MIYU_DB_PATH、MIYU_WXID、MIYU_KEY_HEX 环境变量。</p>
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
  <ReaderRules :groups="groups" :selected="selected" />
</template>
