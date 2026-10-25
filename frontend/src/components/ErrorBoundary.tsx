import { Component, type ReactNode } from 'react'

/** Keeps one broken page from blanking the whole app (and the POS with it). */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="card narrow">
        <h2>Something went wrong on this page</h2>
        <div className="alert alert-error">{this.state.error.message}</div>
        <button className="btn btn-primary" onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    )
  }
}
