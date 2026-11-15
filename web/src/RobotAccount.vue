<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'

const wxid = ref('')
const busy = ref(false)
const ready = ref(false)
const error = ref('')
const saved = ref(false)

async function update(save: boolean) {
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    wxid.value = (await (save ? api.saveRobotAccount(wxid.value) : api.robotAccount())).wxid
    ready.value = true
    saved.value = save
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
      <label>机器人 wxid <input v-model.trim="wxid" :disabled="!ready || busy" maxlength="256" /></label>
      <p>机器人发言仍会进入聊天记录，监听时跳过这些发言的自动响应。</p>
      <a-button type="primary" html-type="submit" :disabled="!ready" :loading="busy">保存</a-button>
      <span v-if="saved" role="status"> 已保存</span>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
    </form>
  </section>
</template>
