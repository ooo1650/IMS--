/** Console sign-in state. Unlike the IMS app there is no offline mode and no
 *  password hash is ever cached on the device: an operator account can create
 *  businesses and admins, so it only works against the live server. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from './shared/api'

interface Me {
  id: number
  username: string
  name: string
  email: string
  is_platform_admin?: boolean
  test_login?: boolean
}

interface AuthState {
  me: Me | null
  loading: boolean
  /** `{otp: true}` = an emailed code is needed: finish with verifyOtp. */
  login: (identifier: string, password: string) => Promise<{ otp: false } | { otp: true; sentTo: string }>
  verifyOtp: (code: string) => Promise<void>
  resendOtp: () => Promise<string>
  testLogin: (username: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      try {
        await api('auth/csrf')
        setMe(await api<Me>('auth/me'))
      } catch {
        /* signed out */
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const signedIn = useCallback(async (m: Me) => {
    await api('auth/csrf') // sign-in rotates the CSRF token
    setMe(m)
  }, [])

  const login = useCallback(async (identifier: string, password: string) => {
    await api('auth/csrf')
    const r = await api<Me | { otp_required: true; sent_to: string }>('auth/login', { body: { username: identifier, password } })
    if ('sent_to' in r) return { otp: true as const, sentTo: r.sent_to }
    await signedIn(r)
    return { otp: false as const }
  }, [signedIn])

  const verifyOtp = useCallback(async (code: string) => {
    await signedIn(await api<Me>('auth/login/verify', { body: { code } }))
  }, [signedIn])

  const resendOtp = useCallback(async () => (await api<{ sent_to: string }>('auth/login/resend', { method: 'POST' })).sent_to, [])

  const testLogin = useCallback(async (username: string) => {
    await api('auth/csrf')
    await signedIn(await api<Me>('auth/test-login', { body: { username } }))
  }, [signedIn])

  const logout = useCallback(async () => {
    try {
      await api('auth/logout', { method: 'POST' })
    } finally {
      setMe(null)
    }
  }, [])

  const value = useMemo(() => ({ me, loading, login, verifyOtp, resendOtp, testLogin, logout }),
    [me, loading, login, verifyOtp, resendOtp, testLogin, logout])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
