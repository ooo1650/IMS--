import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { NetworkError, api, setActiveBranch, setActiveOrg } from './lib/api'
import { cacheLogin, kvGet, kvSet, offlineLogin } from './lib/offline'
import { setOnline } from './lib/sync'
import type { BranchAccess, Me } from './lib/types'

interface AuthState {
  me: Me | null
  loading: boolean
  /** Logged in from the device cache while the server was unreachable. */
  offlineSession: boolean
  branch: BranchAccess | null
  can: (perm: string) => boolean
  canOrg: (perm: string) => boolean
  /** Email or username + password. `otp` = an emailed code is needed: finish with verifyOtp. */
  login: (identifier: string, password: string) => Promise<{ otp: false } | { otp: true; sentTo: string }>
  verifyOtp: (code: string) => Promise<void>
  /** Sends a fresh code; returns the masked address. */
  resendOtp: () => Promise<string>
  /** Testing phase only: sign in as a user without password or code (server must allow it). */
  testLogin: (username: string) => Promise<void>
  /** Invited person: email + setup code + new password; signs them in. */
  signUp: (body: { email: string; code: string; password: string; first_name: string; last_name: string }) => Promise<void>
  /** Re-read the signed-in user (after account changes). */
  refresh: () => Promise<void>
  logout: () => Promise<void>
  selectBranch: (id: number) => Promise<void>
  /** Switch business; the branch list changes with it. */
  selectBusiness: (id: number) => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)
const OFFLINE_MAX_AGE_HOURS = 24

/** ``offline={false}`` (operator console): never cache a password hash on the
 *  device and never sign in from that cache. */
/**
 * A hint (not a credential) that this browser was signed in last time, so the
 * screen shown while the session is checked matches where you'll land: the
 * app outline if you were signed in, the sign-in page if not. The server's
 * answer always decides. Browser storage can be blocked, hence the try/catch.
 */
const SESSION_HINT = 'ims.signedIn'
export function wasSignedIn(): boolean {
  try {
    return localStorage.getItem(SESSION_HINT) === '1'
  } catch {
    return false
  }
}
function rememberSignedIn(on: boolean) {
  try {
    if (on) localStorage.setItem(SESSION_HINT, '1')
    else localStorage.removeItem(SESSION_HINT)
  } catch {
    /* private mode: no hint, the plain loading screen is used */
  }
}

export function AuthProvider({ children, offline = true }: { children: ReactNode; offline?: boolean }) {
  const [me, setMe] = useState<Me | null>(null)
  const [branchId, setBranchId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [offlineSession, setOfflineSession] = useState(false)

  const applyMe = useCallback(async (m: Me): Promise<void> => {
    // Someone in several businesses with none chosen yet: reopen the one used
    // last on this device, else the first. (One business is chosen by the server.)
    if (!m.active_org_id && m.businesses?.length) {
      const last = await kvGet<number>(`org:${m.id}`)
      const pick = m.businesses.find((b) => b.id === last) ?? m.businesses[0]
      try {
        return await applyMe(await api<Me>('auth/active-org', { body: { org_id: pick.id } }))
      } catch {
        /* offline: carry on without a business */
      }
    }
    if (m.active_org_id) void kvSet(`org:${m.id}`, m.active_org_id)
    rememberSignedIn(true)
    setMe(m)
    setActiveOrg(m.active_org_id ?? null)
    const remembered = await kvGet<number>(`branch:${m.id}`)
    const ids = m.branches.map((b) => b.id)
    const id = [m.active_branch_id, remembered, ids.length === 1 ? ids[0] : null].find((x) => x && ids.includes(x)) ?? null
    setBranchId(id)
    setActiveBranch(id)
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        await api('auth/csrf')
        await applyMe(await api<Me>('auth/me'))
      } catch (e) {
        if (e instanceof NetworkError) setOnline(false)
        else rememberSignedIn(false) // the server says: not signed in (session ended or never existed)
      } finally {
        setLoading(false)
      }
    })()
  }, [applyMe])

  // Password kept in memory only between the password and code steps, so the
  // offline sign-in cache is written once the code is accepted.
  const pendingPassword = useRef('')

  const signedIn = useCallback(async (m: Me, password: string) => {
    await api('auth/csrf') // login rotates the CSRF token
    setOfflineSession(false)
    await applyMe(m)
    // Offline sign-in works with the username or the email, whichever is typed.
    if (offline) for (const id of new Set([m.username, m.email].filter(Boolean))) void cacheLogin(id, password, m)
  }, [applyMe, offline])

  const login = useCallback(
    async (identifier: string, password: string) => {
      try {
        await api('auth/csrf')
        const r = await api<Me | { otp_required: true; sent_to: string }>('auth/login', { body: { username: identifier, password } })
        if ('sent_to' in r) {
          pendingPassword.current = password
          return { otp: true as const, sentTo: r.sent_to }
        }
        await signedIn(r, password)
      } catch (e) {
        if (!(e instanceof NetworkError) || !offline) throw e
        // Offline: the device cache only exists after a full online sign-in (code included) in the last 24 h.
        setOnline(false)
        const m = await offlineLogin(identifier, password, OFFLINE_MAX_AGE_HOURS)
        setOfflineSession(true)
        await applyMe(m)
      }
      return { otp: false as const }
    },
    [applyMe, signedIn, offline],
  )

  const verifyOtp = useCallback(async (code: string) => {
    const m = await api<Me>('auth/login/verify', { body: { code } })
    await signedIn(m, pendingPassword.current)
    pendingPassword.current = ''
  }, [signedIn])

  const signUp = useCallback(async (body: { email: string; code: string; password: string; first_name: string; last_name: string }) => {
    await api('auth/csrf')
    const m = await api<Me>('auth/signup', { body })
    await signedIn(m, body.password)
  }, [signedIn])

  const testLogin = useCallback(async (username: string) => {
    await api('auth/csrf')
    const m = await api<Me>('auth/test-login', { body: { username } })
    await api('auth/csrf') // login rotates the CSRF token
    setOfflineSession(false)
    await applyMe(m) // no offline cache: there is no password to verify against
  }, [applyMe])

  const resendOtp = useCallback(async () => (await api<{ sent_to: string }>('auth/login/resend', { method: 'POST' })).sent_to, [])

  const refresh = useCallback(async () => {
    setMe(await api<Me>('auth/me'))
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('auth/logout', { method: 'POST' })
    } catch {
      /* offline logout is local only */
    }
    rememberSignedIn(false)
    setMe(null)
    setBranchId(null)
    setActiveBranch(null)
  }, [])

  const selectBranch = useCallback(
    async (id: number) => {
      setBranchId(id)
      setActiveBranch(id)
      if (me) await kvSet(`branch:${me.id}`, id)
      try {
        await api('auth/active-branch', { body: { branch_id: id } })
      } catch {
        /* header carries the branch anyway */
      }
    },
    [me],
  )

  const selectBusiness = useCallback(async (id: number) => {
    const m = await api<Me>('auth/active-org', { body: { org_id: id } })
    setActiveBranch(null)
    await applyMe(m)
  }, [applyMe])

  const value = useMemo<AuthState>(() => {
    const branch = me?.branches.find((b) => b.id === branchId) ?? null
    return {
      me, loading, offlineSession, branch, login, verifyOtp, resendOtp, testLogin, signUp, refresh, logout, selectBranch, selectBusiness,
      can: (perm) => !!branch?.permissions.includes(perm),
      canOrg: (perm) => !!me?.org_permissions.includes(perm),
    }
  }, [me, loading, offlineSession, branchId, login, verifyOtp, resendOtp, testLogin, signUp, refresh, logout, selectBranch, selectBusiness])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
