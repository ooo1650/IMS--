import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { NetworkError, api, setActiveBranch } from './lib/api'
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
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  selectBranch: (id: number) => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)
const OFFLINE_MAX_AGE_HOURS = 24

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [branchId, setBranchId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [offlineSession, setOfflineSession] = useState(false)

  const applyMe = useCallback(async (m: Me) => {
    setMe(m)
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
      } finally {
        setLoading(false)
      }
    })()
  }, [applyMe])

  const login = useCallback(
    async (username: string, password: string) => {
      try {
        await api('auth/csrf')
        const m = await api<Me>('auth/login', { body: { username, password } })
        await api('auth/csrf') // login rotates the CSRF token
        setOfflineSession(false)
        await applyMe(m)
        void cacheLogin(username, password, m)
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
        setOnline(false)
        const m = await offlineLogin(username, password, OFFLINE_MAX_AGE_HOURS)
        setOfflineSession(true)
        await applyMe(m)
      }
    },
    [applyMe],
  )

  const logout = useCallback(async () => {
    try {
      await api('auth/logout', { method: 'POST' })
    } catch {
      /* offline logout is local only */
    }
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

  const value = useMemo<AuthState>(() => {
    const branch = me?.branches.find((b) => b.id === branchId) ?? null
    return {
      me, loading, offlineSession, branch, login, logout, selectBranch,
      can: (perm) => !!branch?.permissions.includes(perm),
      canOrg: (perm) => !!me?.org_permissions.includes(perm),
    }
  }, [me, loading, offlineSession, branchId, login, logout, selectBranch])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
