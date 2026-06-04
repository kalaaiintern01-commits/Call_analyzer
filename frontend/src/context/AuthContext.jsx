import { createContext, useContext, useState } from 'react'

// Minimal user context for the federation auth handoff (federation-cookbook
// §4.5 + auth-contract §3). The shell injects the user object via URL params
// on first navigation into this remote; App.jsx reads them and calls setUser.
//
// Today nothing in the app gates on `user` — this is scaffolding so the
// remote is shell-ready the moment Platform Architecture registers the
// `call-center` app in the shell.
const AuthContext = createContext({
  user: null,
  // eslint-disable-next-line @typescript-eslint/no-empty-function, no-unused-vars
  setUser: () => {},
})

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  return (
    <AuthContext.Provider value={{ user, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
