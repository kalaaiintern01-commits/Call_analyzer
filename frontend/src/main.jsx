import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import './index.css'

// NOTE: this file is the STANDALONE entry. When this app is consumed via
// federation by the KALA shell, the shell imports `./src/App.jsx` directly
// (see vite.config.js `exposes`) and main.jsx never executes. That means:
//
//   - <BrowserRouter> and <AuthProvider> below only wrap App for standalone
//     dev/preview. In federated mode the shell provides its own router
//     + auth context (federation-cookbook §4.3, §4.5).
//   - <ModeToggle> only renders standalone — the shell owns dark mode in
//     federated mode (per the cookbook 'shell owns the reset' guidance).

// KALA design guide §3 — floating dark-mode toggle. Persists choice in
// localStorage; sets `data-mode="dark"` on <html>. We use removeProperty()
// (not setProperty) when going dark so the [data-mode="dark"] :root override
// can take effect, per KALA-design-guide §3 rule.
function ModeToggle() {
  const [dark, setDark] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem('kala-mode') === 'dark'
  })

  useEffect(() => {
    const html = document.documentElement
    if (dark) {
      html.dataset.mode = 'dark'
      html.style.removeProperty('--base-l')
      html.style.removeProperty('--base-s')
      localStorage.setItem('kala-mode', 'dark')
    } else {
      html.dataset.mode = 'light'
      localStorage.setItem('kala-mode', 'light')
    }
  }, [dark])

  return (
    <button
      type="button"
      className="mode-toggle"
      aria-pressed={dark}
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={dark ? 'Light mode' : 'Dark mode'}
      onClick={() => setDark((d) => !d)}
    >
      {dark ? '☀' : '☾'}
    </button>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ModeToggle />
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
