<script setup lang="ts">
import { ref } from 'vue'
import { previewChatSummary } from './api'

const props = defineProps<{ groupId: string }>()
const visible = ref(false)
const day = ref('')
const busy = ref(false)
const error = ref('')

function show() {
  day.value = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(Date.now() - 86400000)
  error.value = ''
  visible.value = true
}

async function preview() {
  if (!props.groupId || !day.value || busy.value) return
  error.value = ''
  const tab = window.open('about:blank', '_blank')
  if (!tab) {
    error.value = '浏览器拦截了新标签页，请允许弹窗后重试。'
    return
  }
  tab.opener = null
  tab.document.body.textContent = '正在生成群聊梳理 PDF…'
  busy.value = true
  try {
    const pdf = await previewChatSummary(props.groupId, day.value)
    const url = URL.createObjectURL(pdf)
    tab.location.replace(url)
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    visible.value = false
  } catch (cause) {
    tab.close()
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="preview-actions">
    <a-button :disabled="!groupId" @click="show">预览 PDF</a-button>
    <RouterLink to="/rules">查看全局 AI 配置</RouterLink>
  </div>
  <a-modal :visible="visible" title="预览聊天梳理" :footer="false" :closable="!busy"
    :mask-closable="false" @cancel="!busy && (visible = false)">
    <form @submit.prevent="preview">
      <label>梳理日期（北京时间） <input v-model="day" type="date" required :disabled="busy" /></label>
      <p>使用当天聊天记录生成 PDF，已有缓存时直接打开。预览只在本机显示。</p>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
      <a-button type="primary" html-type="submit" :loading="busy">{{ busy ? '正在生成…' : '生成并打开 PDF' }}</a-button>
    </form>
  </a-modal>
</template>

<style scoped>
.preview-actions { display: flex; gap: 12px; align-items: center; margin-top: 16px; }
form { display: grid; gap: 12px; }
form p { margin: 0; color: var(--color-text-2); }
form button { justify-self: end; }
</style>
