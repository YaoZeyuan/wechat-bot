<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Select as ASelect } from '@arco-design/web-vue'
import { api, type GroupPlugin } from './api'

const props = defineProps<{ groupId: string; plugin: GroupPlugin }>()
const emit = defineEmits<{ saved: [GroupPlugin] }>()
const admins = ref([...(props.plugin.admin_ids ?? [])])
const members = ref<{ id: string; name: string }[]>([])
const busy = ref(false)
const loaded = ref(false)
const error = ref('')

onMounted(async () => {
  busy.value = true
  try {
    members.value = await api.groupMembers(props.groupId)
    const current = new Set(members.value.map(member => member.id))
    admins.value = admins.value.filter(id => current.has(id))
    loaded.value = true
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
})

async function save() {
  busy.value = true
  error.value = ''
  try {
    emit('saved', await api.saveGroupPlugin(props.groupId, props.plugin.id, props.plugin.enabled,
      undefined, undefined, undefined, undefined, admins.value))
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="admins" @submit.prevent="save">
    <label for="order-admins">本群管理员</label>
    <a-select id="order-admins" v-model="admins" multiple allow-search :loading="busy" :disabled="busy || !loaded"
      placeholder="从当前群成员中选择"
      :options="members.map(member => ({ label: member.name + ' · ' + member.id, value: member.id }))" />
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <a-button type="primary" html-type="submit" :disabled="!loaded" :loading="busy">保存管理员</a-button>
  </form>
</template>

<style scoped>
.admins { display: grid; gap: 10px; }
.admins button { justify-self: end; }
</style>
