import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // This project keeps a single .env at the repo root (shared with the
  // Python backend) rather than one inside frontend/ -- Vite only looks in
  // its own root by default, so without this, VITE_* variables (the
  // backend API key, API base URL) were silently never read, always
  // falling through to hardcoded fallbacks in the code.
  envDir: path.resolve(import.meta.dirname, '..'),
})
