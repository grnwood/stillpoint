import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import fs from 'fs'
import path from 'path'

// https://vite.dev/config/
const certPath = process.env.DEV_SSL_CERT
const keyPath = process.env.DEV_SSL_KEY
const httpsConfig =
  certPath && keyPath && fs.existsSync(certPath) && fs.existsSync(keyPath)
    ? {
        cert: fs.readFileSync(path.resolve(certPath)),
        key: fs.readFileSync(path.resolve(keyPath))
      }
    : undefined

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'robots.txt'],
      manifest: {
        name: 'StillPoint Web Client',
        short_name: 'StillPoint',
        description: 'Mobile-first web client for StillPoint',
        theme_color: '#ffffff',
        background_color: '#ffffff',
        display: 'standalone',
        icons: [
          {
            src: '/icon-192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png'
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        runtimeCaching: [
          {
            urlPattern: /^http:\/\/localhost:8000\/api\/.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: {
                maxEntries: 100,
                maxAgeSeconds: 60 * 60 * 24 // 24 hours
              }
            }
          }
        ]
      }
    })
  ],
  server: {
    host: true, // Listen on all addresses including LAN
    https: httpsConfig,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/sync': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/recent': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/tags': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
