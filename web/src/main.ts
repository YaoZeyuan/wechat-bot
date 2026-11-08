import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { Alert, Button, Layout, Modal, Tabs, Tag } from '@arco-design/web-vue'
import '@arco-design/web-vue/dist/arco.css'
import './style.css'
import App from './App.vue'
import ReaderSetup from './ReaderSetup.vue'
import ReaderRules from './ReaderRules.vue'
import AdminLayout from './AdminLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: App },
    { path: '/setup', component: ReaderSetup },
    { path: '/rules', component: ReaderRules },
  ],
})

createApp(AdminLayout).use(router).use(Layout).use(Tabs).use(Tag).use(Button).use(Alert).use(Modal).mount('#app')
