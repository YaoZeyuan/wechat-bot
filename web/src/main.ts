import { createApp } from 'vue'
import { createRouter, createWebHistory, RouterView } from 'vue-router'
import './style.css'
import App from './App.vue'
import ReaderSetup from './ReaderSetup.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: App },
    { path: '/setup', component: ReaderSetup },
  ],
})

createApp(RouterView).use(router).mount('#app')
