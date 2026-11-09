<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'
import { readerGroups as groups, selectedReaderGroup as selected } from './readerSelection'

const query = ref('')
const mode = ref('manual')
const searchDirectory = ref('')
const candidates = ref<Awaited<ReturnType<typeof api.findReaderAccounts>>['items']>([])
const databasePath = ref('')
const account = ref('')
const databaseKey = ref('')
const imageXorKey = ref('')
const imageAesKey = ref('')
const port = ref(5032)
const token = ref('')
const busy = ref(false)
const notice = ref('')
const error = ref('')
const keyProgress = ref('')
const keyError = ref('')
const keyEnabled = ref<boolean>()
const confirmRestart = ref(false)
const keySteps = ref<{ stage: string; message: string }[]>([])

onMounted(async () => {
  try {
    const progress = await api.readerKeyProgress()
    keyEnabled.value = progress.enabled
    keySteps.value = progress.steps
  } catch (err) {
    keyError.value = err instanceof Error ? err.message : String(err)
  }
})

onMounted(async () => {
  try {
    const profile = await api.readerAccountSelection()
    if (profile && !databasePath.value) {
      databasePath.value = profile.database_path
      account.value = profile.account
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
})

async function discoverAccounts(pick = false) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (pick) {
      const { directory } = await api.pickReaderDirectory()
      if (!directory) return
      searchDirectory.value = directory
    }
    candidates.value = []
    candidates.value = (await api.findReaderAccounts(searchDirectory.value.trim())).items
    notice.value = candidates.value.length
      ? '请选择账号，并核对填入的账号 ID。'
      : '未找到账号。请从微信的文件管理设置确认存储位置，或填入离线副本路径。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function rememberAccount() {
  busy.value = true
  error.value = ''
  try {
    await api.saveReaderAccountSelection(databasePath.value.trim(), account.value.trim())
    notice.value = '已记住所选账号和目录。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function chooseAccount(candidate: (typeof candidates.value)[number]) {
  databasePath.value = candidate.database_path
  account.value = candidate.account
  databaseKey.value = ''
  imageXorKey.value = ''
  imageAesKey.value = ''
  groups.value = []
  selected.value = ''
  void rememberAccount()
}

async function extractKey() {
  confirmRestart.value = false
  busy.value = true
  error.value = ''
  keyError.value = ''
  notice.value = ''
  keyProgress.value = '正在向后端发送请求…'
  keySteps.value = []
  let polling = false
  const timer = window.setInterval(async () => {
    if (polling) return
    polling = true
    try {
      const progress = await api.readerKeyProgress()
      if (progress.message) keyProgress.value = progress.message
      keySteps.value = progress.steps
    } catch {
      // The extraction request itself reports connection failures.
    } finally {
      polling = false
    }
  }, 750)
  try {
    databaseKey.value = (await api.extractReaderKey()).key
    notice.value = '密钥已填入，请验证它是否对应所选数据库。'
  } catch (err) {
    keyError.value = err instanceof Error ? err.message : String(err)
  } finally {
    window.clearInterval(timer)
    try {
      keySteps.value = (await api.readerKeyProgress()).steps
    } catch {
      // Keep the last reported steps if the backend disconnected.
    }
    keyProgress.value = ''
    busy.value = false
  }
}

async function probe(checkMessages = false, saveProfile = false) {
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
        image_xor_key: imageXorKey.value.trim(),
        image_aes_key: imageAesKey.value.trim(),
        save_profile: saveProfile,
      } : {}),
      ...(checkMessages ? { session_id: selected.value } : {}),
    })
    groups.value = result.groups
    if (!groups.value.some((group) => group.id === selected.value)) selected.value = ''
    notice.value = result.message_readable
      ? result.has_messages ? '已验证所选群的消息读取。' : '消息库查询成功，但所选群目前没有本地消息记录。'
      : `连接成功，找到 ${result.groups.length} 个群。${result.has_more ? '可输入群名缩小范围。' : ''}`
    if (saveProfile) notice.value += ' 已保存本机配置。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="page">
  <section class="card">
    <h2>电脑端微信读取</h2>
    <p>先查找账号目录，再确认账号 ID 并提供数据库密钥。建议使用离线副本验证读取。</p>
    <label>
      <select v-model="mode">
        <option value="automatic">SQLCipher（自动读取本机设置）</option>
        <option value="manual">SQLCipher（独立配置向导）</option>
        <option value="proxy">手动填写代理端口和令牌</option>
      </select>
    </label>
    <template v-if="mode === 'proxy'">
      <label>本机端口 <input v-model.number="port" type="number" min="1" max="65535" /></label>
      <label>访问令牌 <input v-model="token" type="password" autocomplete="off" /></label>
    </template>
    <template v-if="mode === 'manual'">
      <h3>1. 查找并确认账号</h3>
      <a-button :disabled="busy" @click="discoverAccounts()">查找微信账号</a-button>
      <a-button :disabled="busy" @click="discoverAccounts(true)">手动选择数据库目录…</a-button>
      <details>
        <summary>目录对话框无法打开时，输入路径</summary>
        <label>搜索目录<input v-model="searchDirectory" placeholder="留空搜索默认目录" /></label>
      </details>
      <ul v-if="candidates.length">
        <li v-for="candidate in candidates" :key="candidate.database_path">
          <a-button @click="chooseAccount(candidate)">{{ candidate.account }}</a-button>
          {{ candidate.database_path }}
        </li>
      </ul>
      <label>数据库目录 <input v-model="databasePath" placeholder="账号目录或 db_storage" /></label>
      <label>微信账号 ID <input v-model="account" /></label>
      <a-button :disabled="busy || !databasePath.trim() || !account.trim()" @click="rememberAccount">记住账号和目录</a-button>
      <h3>2. 提供数据库密钥</h3>
      <label>数据库密钥 <input v-model="databaseKey" type="password" autocomplete="off" /></label>
      <a-alert v-if="keyEnabled === false" type="warning">
        后端未开启自动取钥。停止旧后端，在 PowerShell 执行以下两行，再刷新页面：
        <pre>$env:WECHAT_BOT_KEY_EXTRACTION = '1'
uv run dev-server</pre>
      </a-alert>
      <a-button :disabled="busy || keyEnabled !== true" @click="confirmRestart = true">重启微信并获取密钥</a-button>
      <a-modal v-model:visible="confirmRestart" title="重启微信并获取密钥" ok-text="确认重启" @ok="extractKey">
        后端将关闭并重新启动微信。微信重新打开后，请登录要读取的账号。
      </a-modal>
      <a-alert v-if="keyError" type="error">{{ keyError }}</a-alert>
      <a-alert v-if="keyProgress" type="info">{{ keyProgress }}</a-alert>
      <ol v-if="keySteps.length">
        <li v-for="step in keySteps" :key="step.stage">{{ step.message }}</li>
      </ol>
      <p>如果已有密钥，可直接填写。自动获取需启用后端提取开关。启动提取后，如微信提示登录，请在等待期间完成登录。</p>
      <p>保存后，后台监听会使用本机配置。已保存账号再次验证时可以留空密钥。</p>
      <h3>3. 图片解密密钥（可选）</h3>
      <label>图片 XOR 密钥 <input v-model="imageXorKey" type="password" autocomplete="off" /></label>
      <label>图片 AES 密钥 <input v-model="imageAesKey" type="password" autocomplete="off" /></label>
      <p>已有密钥可手动填写。此步骤保存配置，图片解密和自动扫描尚未接入。</p>
      <a-button :disabled="busy || !databasePath.trim() || !account.trim()" @click="probe(false, true)">验证并保存配置</a-button>
    </template>
    <p v-if="busy" role="status">正在处理，请留意本机弹窗或微信登录提示…</p>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message ok" role="status">{{ notice }}</p>
    <form @submit.prevent="probe()">
      <label>群名 <input v-model="query" placeholder="输入群名以缩小范围" /></label>
      <a-button type="primary" html-type="submit" :loading="busy">测试连接并搜索群聊</a-button>
    </form>
    <template v-if="groups.length">
      <label>验证消息读取
        <select v-model="selected">
          <option value="">请选择群</option>
          <option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }} · {{ group.id }}</option>
        </select>
      </label>
      <a-button :disabled="busy || !selected" @click="probe(true)">验证所选群</a-button>
    </template>
  </section>
  </main>
</template>
