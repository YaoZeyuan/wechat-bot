<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Select as ASelect } from '@arco-design/web-vue'
import { api } from './api'

const wxid = ref('')
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const saved = ref(false)
const contacts = ref<Awaited<ReturnType<typeof api.robotContacts>>>([])
let searchVersion = 0

async function search(q: string) {
  const version = ++searchVersion
  try {
    const matches = await api.robotContacts(q.trim())
    if (version !== searchVersion) return
    const current = contacts.value.find(contact => contact.id === wxid.value)
    contacts.value = current && !matches.some(contact => contact.id === current.id) ? [current, ...matches] : matches
  } catch (cause) {
    if (version === searchVersion) error.value = (cause as Error).message
  }
}

async function update(save: boolean) {
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    wxid.value = (await (save ? api.saveRobotAccount(wxid.value) : api.robotAccount())).wxid
    ready.value = true
    saved.value = save
    if (!save) await search(wxid.value)
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
onMounted(() => void update(false))
</script>

<template>
  <section class="card">
    <h2>机器人账号</h2>
    <form @submit.prevent="update(true)">
      <label>机器人账号
        <a-select :model-value="wxid || undefined" :disabled="!ready || busy" allow-search allow-clear
          :filter-option="false" placeholder="搜索昵称、备注或 wxid"
          :options="contacts.map(contact => ({
            value: contact.id,
            label: [contact.remark, contact.nickname !== contact.remark ? contact.nickname : '', contact.id].filter(Boolean).join(' · '),
          }))"
          @search="search" @popup-visible-change="visible => visible && search('')"
          @update:model-value="value => { wxid = String(value ?? ''); saved = false }" />
      </label>
      <p v-if="wxid">wxid <code>{{ wxid }}</code></p>
      <p>机器人发言仍会进入聊天记录，监听时跳过这些发言的自动响应。</p>
      <a-button type="primary" html-type="submit" :disabled="!ready" :loading="busy">保存</a-button>
      <span v-if="saved" role="status"> 已保存</span>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
    </form>
  </section>
</template>
