import { useState } from 'react'
import type { FormEvent } from 'react'
import {
  OPERATOR_TOKEN_STORAGE_KEY,
  readStoredOperatorToken,
  takeOperatorTokenFragment,
} from '../../shared/operatorToken'

/** The operator token the console holds, and the prompt that asks for one. */
export interface OperatorTokenState {
  /** The token in use, or null when none is held. */
  token: string | null
  /** True when the server refused the token, until a new one is submitted. */
  authFailed: boolean
  tokenInput: string
  setTokenInput: (value: string) => void
  /** Use the typed token: saved in this browser, then sent on the next connection. */
  submitToken: (event: FormEvent) => void
  /** Forget the token in this browser and in the console. */
  logout: () => void
  /**
   * The operator socket closed. A refused token (close code 1008) is forgotten, so the
   * operator is asked for one again; any other close leaves the token alone.
   */
  onSocketClose: (event: CloseEvent) => void
}

/**
 * The operator console's token: where it comes from, where it is kept, and what happens
 * when the server refuses it. The token is sent in the socket's first message by
 * `useOperatorState`; this hook only holds it.
 */
export function useOperatorToken(): OperatorTokenState {
  const [token, setToken] = useState<string | null>(() => {
    // A console the launcher opened carries its token in the fragment. It is saved
    // the same way a typed token is (submitToken), so a refusal and the rest of the
    // flow are identical. This runs during the first render, so the socket's first
    // message already carries it.
    const fromLink = takeOperatorTokenFragment()
    if (fromLink === null) return readStoredOperatorToken()
    try {
      localStorage.setItem(OPERATOR_TOKEN_STORAGE_KEY, fromLink)
    } catch {
      // Storage is blocked (private browsing): the link's token still works on this page.
    }
    return fromLink
  })
  const [tokenInput, setTokenInput] = useState('')
  const [authFailed, setAuthFailed] = useState(false)

  const submitToken = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (trimmed) {
      localStorage.setItem(OPERATOR_TOKEN_STORAGE_KEY, trimmed)
      setToken(trimmed)
      setTokenInput('')
      setAuthFailed(false)
    }
  }

  const logout = () => {
    localStorage.removeItem(OPERATOR_TOKEN_STORAGE_KEY)
    setToken(null)
    setAuthFailed(false)
  }

  const onSocketClose = (event: CloseEvent) => {
    // WebSocket close code 1008 = policy violation (auth failure)
    if (event.code !== 1008) return
    // A refusal answers the first message, before anything else: with no token tried
    // there is nothing to call failed, so the prompt opens without the error text.
    if (token !== null) {
      setAuthFailed(true)
      // Clear invalid token
      localStorage.removeItem(OPERATOR_TOKEN_STORAGE_KEY)
      setToken(null)
    }
  }

  return { token, authFailed, tokenInput, setTokenInput, submitToken, logout, onSocketClose }
}
