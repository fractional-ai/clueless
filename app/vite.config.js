import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Port 3001: 3000 and 5173 are taken by other local apps on this machine.
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 3001, strictPort: true },
})
