import { Component } from 'react'

/**
 * Catches render errors in any child panel so one broken component
 * doesn't take down the entire dashboard.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="rounded border border-red-800 bg-red-950/30 p-3 text-xs text-red-400">
          Panel error: {this.state.error.message}
        </div>
      )
    }
    return this.props.children
  }
}
