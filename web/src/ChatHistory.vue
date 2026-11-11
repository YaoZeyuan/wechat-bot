<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, type Conversation, type ConversationDetails, type HistoryMessage, type MonitoredGroup } from './api'

const filters = [
  { key: 'all', label: '全部' }, { key: 'private', label: '联系人' }, { key: 'group', label: '群聊' },
]
const query = ref('')
const kind = ref('all')
const conversations = ref<Conversation[]>([])
const selected = ref<Conversation>()
const route = useRoute()
const detailsVisible = ref(false)
const details = ref<ConversationDetails>()
const detailsError = ref('')
const robotGroups = ref<MonitoredGroup[]>()
const robotBusy = ref(false)
const robotError = ref('')
const robotEnabled = computed(() => robotGroups.value?.some(group => group.group_id === selected.value?.id && group.enabled))
const robotLimitReached = computed(() => !robotEnabled.value && (robotGroups.value?.filter(group => group.enabled).length ?? 0) >= 10)
const robotLimitReason = '最多同时启用 10 个群机器人，请先在「规则与任务」暂停一个群。'
let detailsVersion = 0
const messages = ref<HistoryMessage[]>([])
const timeline = ref<HTMLElement>()
const moreConversations = ref(false)
const moreMessages = ref(false)
const listBusy = ref(false)
const messagesBusy = ref(false)
const listError = ref('')
const messageError = ref('')
const ordered = computed(() => [...messages.value].reverse())
let listVersion = 0
let messageVersion = 0
let messageOffset = 0
let appliedQuery = ''

function date(value: number) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''
}

function content(message: HistoryMessage) {
  if (message.kind === 'text') return message.text || '（空白文本）'
  const labels: Record<number, string> = { 3: '图片', 34: '语音', 43: '视频', 47: '表情', 49: '链接或文件', 10000: '系统消息' }
  return `[${labels[message.type_code % 4294967296] ?? '其他消息'}]`
}

async function loadConversations(append = false) {
  const version = ++listVersion
  listBusy.value = true
  listError.value = ''
  if (!append) {
    appliedQuery = query.value.trim()
    conversations.value = []
    moreConversations.value = false
  }
  try {
    const result = await api.conversations(appliedQuery, kind.value, conversations.value.length)
    if (version !== listVersion) return
    conversations.value.push(...result.items)
    moreConversations.value = result.has_more
  } catch (error) {
    if (version === listVersion) listError.value = (error as Error).message
  } finally {
    if (version === listVersion) listBusy.value = false
  }
}

function chooseFilter(value: string) {
  kind.value = value
  void loadConversations()
}

async function openConversation(conversation: Conversation, append = false) {
  const version = ++messageVersion
  selected.value = conversation
  messagesBusy.value = true
  messageError.value = ''
  const previousHeight = timeline.value?.scrollHeight ?? 0
  const previousTop = timeline.value?.scrollTop ?? 0
  if (!append) {
    if (conversation.kind === 'group') void loadRobotGroups()
    detailsVisible.value = false
    detailsVersion++
    messages.value = []
    messageOffset = 0
    moreMessages.value = false
  }
  try {
    const result = await api.conversationMessages(conversation.id, messageOffset)
    if (version !== messageVersion) return
    const seen = new Set(messages.value.map((message) => message.messageId))
    messages.value.push(...result.items.filter((message) => !seen.has(message.messageId)))
    messageOffset += result.items.length
    moreMessages.value = result.has_more
    await nextTick()
    if (version === messageVersion && timeline.value) {
      timeline.value.scrollTop = append ? previousTop + timeline.value.scrollHeight - previousHeight : timeline.value.scrollHeight
    }
  } catch (error) {
    if (version === messageVersion) messageError.value = (error as Error).message
  } finally {
    if (version === messageVersion) messagesBusy.value = false
  }
}

async function loadRobotGroups() {
  robotError.value = ''
  try {
    robotGroups.value = await api.monitoredGroups()
  } catch (error) {
    robotError.value = (error as Error).message
  }
}

async function enableRobot() {
  const group = selected.value
  if (!group || group.kind !== 'group' || robotLimitReached.value || robotBusy.value) return
  robotBusy.value = true
  robotError.value = ''
  try {
    const saved = await api.saveMonitoredGroup({ group_id: group.id, name: group.name, enabled: true })
    robotGroups.value = [...(robotGroups.value ?? []).filter(item => item.group_id !== saved.group_id), saved]
  } catch (error) {
    await loadRobotGroups()
    robotError.value = (error as Error).message
  } finally {
    robotBusy.value = false
  }
}

async function showDetails() {
  if (!selected.value) return
  const version = ++detailsVersion
  detailsVisible.value = true
  details.value = undefined
  detailsError.value = ''
  try {
    const result = await api.conversationDetails(selected.value.id)
    if (version === detailsVersion) details.value = result
  } catch (error) {
    if (version === detailsVersion) detailsError.value = (error as Error).message
  }
}

onMounted(async () => {
  void loadConversations()
  const id = route.query.session
  if (typeof id !== 'string' || !id) return
  try {
    const result = await api.conversationDetails(id)
    if (!selected.value) await openConversation({ ...result, timestamp_ms: 0 })
  } catch (error) {
    if (!selected.value) messageError.value = (error as Error).message
  }
})
</script>

<template>
  <main class="history-page">
    <header class="history-title">
      <h1>聊天记录</h1>
      <RouterLink to="/setup">读取配置</RouterLink>
    </header>
    <div class="workspace">
      <aside class="conversations" aria-label="会话列表">
        <form class="search" @submit.prevent="loadConversations()">
          <input v-model="query" aria-label="搜索会话" placeholder="搜索联系人、群名或 ID" maxlength="100" />
          <a-button html-type="submit" type="primary" :loading="listBusy">搜索</a-button>
        </form>
        <div class="filters" aria-label="会话类型">
          <a-button v-for="filter in filters" :key="filter.key" size="small"
            :type="kind === filter.key ? 'primary' : 'secondary'" @click="chooseFilter(filter.key)">
            {{ filter.label }}
          </a-button>
        </div>
        <a-alert v-if="listError" type="error">{{ listError }}</a-alert>
        <p v-else-if="listBusy && !conversations.length" role="status">正在读取会话…</p>
        <p v-else-if="!conversations.length">没有找到会话。</p>
        <button v-for="conversation in conversations" :key="conversation.id" class="conversation"
          :class="{ active: selected?.id === conversation.id }"
          :aria-pressed="selected?.id === conversation.id" @click="openConversation(conversation)">
          <span class="avatar" aria-hidden="true">{{ conversation.name.slice(0, 1) }}</span>
          <span class="conversation-label">
            <strong>{{ conversation.name }}</strong>
            <small>{{ conversation.kind === 'group' ? '群聊' : '私聊' }} · {{ date(conversation.timestamp_ms) }}</small>
          </span>
        </button>
        <a-button v-if="moreConversations" long :loading="listBusy" @click="loadConversations(true)">更多会话</a-button>
      </aside>
      <section class="chat" aria-label="消息时间线">
        <template v-if="selected">
          <header class="chat-header">
            <div><h2>{{ selected.name }}</h2><small>{{ selected.id }}</small></div>
            <div class="chat-actions">
              <template v-if="selected.kind === 'group'">
                <RouterLink v-if="robotEnabled" :to="{ path: '/group', query: { group: selected.id } }">配置机器人</RouterLink>
                <template v-else>
                  <a-button type="primary" :loading="robotBusy" :disabled="!robotGroups || robotLimitReached" @click="enableRobot">启用机器人</a-button>
                  <span v-if="robotLimitReached" class="limit-help" tabindex="0" :aria-label="robotLimitReason">
                    <span aria-hidden="true">?</span><span class="limit-tooltip" role="tooltip">{{ robotLimitReason }}</span>
                  </span>
                </template>
              </template>
              <a-button @click="showDetails">详情</a-button>
              <a-button :loading="messagesBusy" @click="openConversation(selected)">刷新</a-button>
            </div>
          </header>
          <a-alert v-if="selected.kind === 'group' && robotError" type="error">{{ robotError }}</a-alert>
          <a-alert v-if="messageError" type="error">{{ messageError }}</a-alert>
          <div ref="timeline" class="timeline">
            <a-button v-if="moreMessages" class="older" :loading="messagesBusy" @click="openConversation(selected, true)">加载更早消息</a-button>
            <p v-if="messagesBusy && !messages.length" role="status">正在读取消息…</p>
            <p v-else-if="!messages.length && !messageError">本地库中没有此会话的消息。</p>
            <article v-for="message in ordered" :key="message.messageId" class="message-row"
              :class="{ outgoing: message.direction === 'out' }">
              <small>{{ message.direction === 'out' ? '我' : message.sender.display_name || (selected.kind === 'private' ? selected.name : '未知发送者') }}
                · {{ date(message.timestampMs) }}</small>
              <div class="bubble">
                <template v-if="message.kind === 'quote'">
                  <div v-if="message.text">{{ message.text }}</div>
                  <div v-for="(quote, index) in message.quotes" :key="index" class="quoted-message"
                    :style="{ marginInlineStart: `${index * 12}px` }">
                    <strong v-if="quote.sender">{{ quote.sender }}： </strong>{{ quote.text }}
                  </div>
                  <small v-if="!message.quotes?.length">[引用内容不可用]</small>
                </template>
                <a v-else-if="message.link" :href="message.link.url" target="_blank" rel="noopener noreferrer" class="shared-link">
                  <img v-if="message.link.cover" :src="message.link.cover" alt="" loading="lazy" referrerpolicy="no-referrer" />
                  <strong>{{ message.link.title || '网页链接' }}</strong>
                  <span v-if="message.link.description" class="link-description">{{ message.link.description }}</span>
                  <small v-if="message.link.source">{{ message.link.source }}</small>
                  <small>{{ message.link.url }}</small>
                </a>
                <template v-else-if="message.file_name">[文件] {{ message.file_name }}</template>
                <template v-else>{{ content(message) }}</template>
              </div>
            </article>
          </div>
        </template>
        <div v-else class="empty-chat">{{ messageError || '从会话列表选择联系人或群聊查看消息。' }}</div>
      </section>
    </div>
    <a-modal v-model:visible="detailsVisible" title="会话详情" :footer="false">
      <a-alert v-if="detailsError" type="error">{{ detailsError }}</a-alert>
      <dl v-else-if="details" class="details">
        <dt>{{ details.kind === 'group' ? '群名' : '昵称' }}</dt><dd>{{ details.original_name || '未记录' }}</dd>
        <dt>备注名</dt><dd>{{ details.remark || '未设置' }}</dd>
        <dt>{{ details.kind === 'group' ? '群 ID' : '用户 ID' }}</dt><dd>{{ details.id }}</dd>
      </dl>
      <p v-else role="status">正在读取详情…</p>
    </a-modal>
  </main>
</template>

<style scoped>
.history-page { max-width: 1440px; margin: auto; padding: 24px clamp(12px, 3vw, 40px); }
.history-title, .chat-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.history-title { margin-bottom: 16px; }
.workspace {
  display: grid; grid-template-columns: minmax(230px, 310px) minmax(0, 1fr);
  height: clamp(440px, calc(100dvh - 190px), 900px);
  border: 1px solid var(--color-border-2); border-radius: 10px; overflow: hidden;
  background: var(--color-bg-2);
}
.conversations { padding: 12px; overflow: auto; border-right: 1px solid var(--color-border-2); }
.search, .filters { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.search input { min-width: 0; flex: 1; margin: 0; }
.conversation { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; background: transparent; padding: 9px; }
.conversation:hover { background: var(--color-fill-2); }
.conversation.active { background: rgb(var(--primary-1)); }
.avatar { display: grid; place-items: center; flex: 0 0 34px; height: 34px; border-radius: 8px; background: rgb(var(--primary-2)); color: rgb(var(--primary-7)); }
.conversation-label { min-width: 0; }
.conversation-label strong, .conversation-label small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
small { color: var(--color-text-3); }
.chat { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--color-fill-1); }
.chat-header { padding: 12px 18px; background: var(--color-bg-2); border-bottom: 1px solid var(--color-border-2); }
.chat-header div { min-width: 0; overflow-wrap: anywhere; }
.chat-header h2 { margin: 0; }
.chat-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.limit-help { position: relative; display: inline-grid; place-items: center; width: 20px; height: 20px; border: 1px solid var(--color-border-3); border-radius: 50%; cursor: help; }
.limit-tooltip { display: none; position: absolute; z-index: 2; right: 0; top: 26px; width: 200px; padding: 8px; border-radius: 4px; color: white; background: #1d2129; }
.limit-help:hover .limit-tooltip, .limit-help:focus .limit-tooltip { display: block; }
.details { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; }
.details dd { margin: 0; overflow-wrap: anywhere; }
.timeline { flex: 1; overflow: auto; padding: 18px; }
.older { display: block; margin: 0 auto 12px; }
.message-row { display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 14px; }
.message-row.outgoing { align-items: flex-end; }
.bubble { max-width: 82%; white-space: pre-wrap; overflow-wrap: anywhere; padding: 8px 12px; border-radius: 8px; background: var(--color-bg-2); }
.outgoing .bubble { background: rgb(var(--primary-2)); }
.shared-link { display: grid; gap: 4px; }
.shared-link img { width: 100%; max-height: 160px; object-fit: cover; border-radius: 4px; }
.link-description { color: var(--color-text-2); }
.quoted-message { margin-top: 6px; padding-left: 8px; border-left: 2px solid var(--color-text-3); font-size: 0.9em; }
.empty-chat { margin: auto; padding: 16px; color: var(--color-text-3); }
@media (max-width: 650px) {
  .workspace { grid-template-columns: 1fr; grid-template-rows: minmax(110px, 27%) minmax(0, 1fr); height: max(540px, calc(100dvh - 190px)); }
  .conversations { border-right: 0; border-bottom: 1px solid var(--color-border-2); }
}
</style>
