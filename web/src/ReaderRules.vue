<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type KeywordRule } from './api'

const props = defineProps<{ groups: { id: string; name: string }[]; selected: string }>()
const rule = ref<KeywordRule>({ group_id: '', keyword: '', handler: 'fixed_reply', url: null, enabled: true })
const rules = ref<KeywordRule[]>([])
const handlers = ref<string[]>([])
const deliveries = ref<Awaited<ReturnType<typeof api.readerDeliveries>>['items']>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')

watch(() => props.selected, (id) => { if (id) rule.value.group_id = id })

async function load() {
  const [config, runs] = await Promise.all([api.readerRules(), api.readerDeliveries()])
  rules.value = config.items
  handlers.value = config.handlers
  deliveries.value = runs.items
}

async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function save() {
  return run(async () => {
    await api.saveReaderRule({
      ...rule.value,
      url: rule.value.handler === 'webhook' ? rule.value.url : null,
    })
    await load()
    notice.value = '规则已保存。'
  })
}

onMounted(() => run(load))
</script>

<template>
  <section class="card">
    <h2>关键词规则</h2>
    <form @submit.prevent="save">
      <label>群 ID
        <input v-model.trim="rule.group_id" list="reader-groups" required />
        <datalist id="reader-groups">
          <option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}</option>
        </datalist>
      </label>
      <label>关键词（整条匹配）<input v-model.trim="rule.keyword" required placeholder="测试" /></label>
      <label>处理函数
        <select v-model="rule.handler">
          <option v-for="handler in handlers" :key="handler" :value="handler">
            {{ handler === 'webhook' ? 'HTTP 回调' : handler === 'fixed_reply' ? '固定回复 · 测试成功' : handler }}
          </option>
        </select>
      </label>
      <label v-if="rule.handler === 'webhook'">回调地址
        <input v-model.trim="rule.url" type="url" required placeholder="http://127.0.0.1:9000/hook" />
      </label>
      <label class="check"><input v-model="rule.enabled" type="checkbox" />启用规则</label>
      <button :disabled="busy || !handlers.length">保存规则</button>
    </form>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message ok" role="status">{{ notice }}</p>
    <ul v-if="rules.length">
      <li v-for="item in rules" :key="JSON.stringify([item.group_id, item.keyword])">
        {{ item.group_id }} · {{ item.keyword }} · {{ item.handler }} · {{ item.enabled ? '启用' : '停用' }}
        <button type="button" :disabled="busy" @click="rule = { ...item }">编辑</button>
      </li>
    </ul>
  </section>
  <section class="card">
    <div class="card-head">
      <h2>规则执行和待发送记录</h2>
      <button :disabled="busy" @click="run(load)">刷新</button>
    </div>
    <p v-if="!deliveries.length">暂无执行记录。</p>
    <ul v-else>
      <li v-for="item in deliveries" :key="item.id">
        {{ item.created_at }} UTC · {{ item.group_id }} · {{ item.handler }} · {{ item.status }}
        <span v-if="item.error"> · {{ item.error }}</span>
        <details v-if="item.task">
          <summary>待发送任务</summary>
          <pre>{{ JSON.stringify(JSON.parse(item.task), null, 2) }}</pre>
        </details>
      </li>
    </ul>
  </section>
</template>
