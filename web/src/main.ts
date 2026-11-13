import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { Alert, Button, Layout, Modal, Tabs, Tag } from '@arco-design/web-vue'
import '@arco-design/web-vue/dist/arco.css'
import './style.css'
import App from './App.vue'
import ReaderSetup from './ReaderSetup.vue'
import ReaderRules from './ReaderRules.vue'
import GroupConfiguration from './GroupConfiguration.vue'
import AdminLayout from './AdminLayout.vue'
import ChatHistory from './ChatHistory.vue'
import PluginCatalog from './PluginCatalog.vue'
import SenderHistory from './SenderHistory.vue'
import WirelessDebug from './WirelessDebug.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/sender' },
    { path: '/sender', component: App },
    { path: '/sender-history', component: SenderHistory },
    { path: '/wireless-debug', component: WirelessDebug },
    { path: '/setup', component: ReaderSetup },
    { path: '/history', component: ChatHistory },
    { path: '/rules', component: ReaderRules, beforeEnter: to =>
      typeof to.query.group === 'string' ? { path: '/group', query: { group: to.query.group } } : true },
    { path: '/group', component: GroupConfiguration },
    { path: '/plugins', component: PluginCatalog },
  ],
})

createApp(AdminLayout).use(router).use(Layout).use(Tabs).use(Tag).use(Button).use(Alert).use(Modal).mount('#app')
