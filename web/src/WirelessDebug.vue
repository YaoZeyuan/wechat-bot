<script setup lang="ts">
import { ref } from 'vue'
import { api, type WirelessStatus } from './api'

const pairAddress = ref('')
const code = ref('')
const connectAddress = ref('')
const busy = ref<'pair' | 'connect' | null>(null)
const error = ref('')
const notice = ref('')
const paired = ref(false)
const status = ref<WirelessStatus>()

async function run(action: 'pair' | 'connect') {
  if (busy.value) return
  busy.value = action
  error.value = ''
  notice.value = ''
  try {
    if (action === 'pair') {
      const pairing = api.pairWireless(pairAddress.value, code.value)
      code.value = ''
      status.value = await pairing
      paired.value = true
      if (!status.value.device) notice.value = '配对成功，暂未自动发现手机的无线连接端口。'
    } else {
      status.value = await api.connectWireless(connectAddress.value)
      if (!status.value.device) notice.value = 'ADB 已接受连接请求，设备尚未就绪。请稍等后重试。'
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <main class="page wireless">
    <h1>无线调试</h1>
    <p>让手机和运行 Server 的电脑连接同一 Wi-Fi，然后在手机的开发者选项中开启「无线调试」。</p>
    <a-alert v-if="error" type="error" role="alert">{{ error }}</a-alert>
    <a-alert v-if="notice" type="info" role="status">{{ notice }}</a-alert>
    <section v-if="status?.device" class="card">
      <h2>手机已通过无线调试连接</h2>
      <div>型号 {{ status.device.model || '未知' }}</div>
      <div>ADB 设备标识 {{ status.device.serial }}</div>
      <p v-if="status.device_count > 1">ADB 检测到多个连接。请拔掉 USB 数据线或断开其他设备，再使用 Sender。</p>
      <RouterLink to="/sender">前往 Sender</RouterLink>
    </section>
    <form v-if="!paired && !status?.device" class="card" @submit.prevent="run('pair')">
      <h2>使用配对码配对</h2>
      <p>在手机上点「使用配对码配对设备」，保持弹窗打开，填入弹窗里的地址和配对码。</p>
      <label>配对地址 <input v-model.trim="pairAddress" required placeholder="192.168.1.10:37123" :disabled="!!busy" /></label>
      <label>六位配对码 <input v-model.trim="code" type="password" inputmode="numeric" autocomplete="off"
        maxlength="6" pattern="[0-9]{6}" required :disabled="!!busy" /></label>
      <a-button html-type="submit" type="primary" :loading="busy === 'pair'" :disabled="!!busy">{{ busy === 'pair' ? '正在配对并发现手机…' : '配对' }}</a-button>
    </form>
    <form v-if="paired && !status?.device" class="card" @submit.prevent="run('connect')">
      <h2>连接手机</h2>
      <p>填写无线调试主页面的「IP 地址和端口」。手机已经配对时可以直接连接。</p>
      <label>连接地址 <input v-model.trim="connectAddress" required placeholder="192.168.1.10:41234" :disabled="!!busy" /></label>
      <a-button html-type="submit" type="primary" :loading="busy === 'connect'" :disabled="!!busy">连接</a-button>
      <a-button :disabled="!!busy" @click="paired = false">重新配对</a-button>
    </form>
  </main>
</template>

<style scoped>
.wireless { max-width: 760px; }
.card { display: grid; gap: 12px; margin-block: 16px; justify-items: start; }
.card h2, .card p { margin: 0; }
.card label { display: grid; gap: 4px; width: min(100%, 360px); }
</style>
