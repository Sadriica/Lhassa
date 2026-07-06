import { useState, useCallback } from 'react'
import OverviewPanel from './components/OverviewPanel'
import InboxPanel from './components/InboxPanel'
import WatchlistPanel from './components/WatchlistPanel'
import CapabilitiesPanel from './components/CapabilitiesPanel'
import BrainsPanel from './components/BrainsPanel'
import SkillsPanel from './components/SkillsPanel'
import AgentsPanel from './components/AgentsPanel'
import AgenticPanel from './components/AgenticPanel'
import WorkflowsPanel from './components/WorkflowsPanel'
import MetricsPanel from './components/MetricsPanel'
import AIUsagePanel from './components/AIUsagePanel'
import MemoryPanel from './components/MemoryPanel'
import HistoryPanel from './components/HistoryPanel'
import ScheduledPanel from './components/ScheduledPanel'
import AuditPanel from './components/AuditPanel'
import RecentEpisodesPanel from './components/RecentEpisodesPanel'
import ApprovalQueuePanel from './components/ApprovalQueuePanel'
import QuarantinePanel from './components/QuarantinePanel'
import ReportsPanel from './components/ReportsPanel'
import ExecutionAnalyticsPanel from './components/ExecutionAnalyticsPanel'
import FleetPanel from './components/FleetPanel'
import HealthPanel from './components/HealthPanel'
import SettingsPanel from './components/SettingsPanel'
import ErrorBoundary from './components/ErrorBoundary'
import ToastHost from './components/ToastHost'
import CommandPalette from './components/CommandPalette'
import PanelShell from './components/layout/PanelShell'
import useWebSocketEvents from './hooks/useWebSocketEvents'
import useSectionRouting from './hooks/useSectionRouting'
import { SECTIONS } from './lib/sections'
import './index.css'

export default function App() {
  // Live state from the `/ws` fan-out, and the active section ↔ URL hash routing.
  const { connected, voiceActive, metrics, agents, agentList, scheduledTick, auditEvents } = useWebSocketEvents()
  const { section, subRoute, goToSection, onFlowRoute } = useSectionRouting()

  // Mobile drawer state lives here so both the nav chrome and the command palette
  // navigation close it. `navigate` = switch section (+ clear sub-route) and close.
  const [navOpen, setNavOpen] = useState(false) // ≥1024px: static sidebar
  const openNav = useCallback(() => setNavOpen(true), [])
  const closeNav = useCallback(() => setNavOpen(false), [])
  const navigate = (id) => { goToSection(id); setNavOpen(false) }

  return (
    <>
      <PanelShell
        connected={connected}
        voiceActive={voiceActive}
        sections={SECTIONS}
        section={section}
        onNavigate={navigate}
        navOpen={navOpen}
        onOpenNav={openNav}
        onCloseNav={closeNav}
        overlays={
          <>
            <ToastHost />
            <CommandPalette sections={SECTIONS} onNavigate={navigate} />
          </>
        }
      >
        {section === 'overview' && (
          <ErrorBoundary><OverviewPanel /></ErrorBoundary>
        )}

        {section === 'inbox' && (
          <ErrorBoundary><InboxPanel /></ErrorBoundary>
        )}

        {section === 'watchlist' && (
          <ErrorBoundary><WatchlistPanel /></ErrorBoundary>
        )}

        {section === 'capabilities' && (
          <ErrorBoundary><CapabilitiesPanel /></ErrorBoundary>
        )}

        {section === 'brains' && (
          <ErrorBoundary><BrainsPanel /></ErrorBoundary>
        )}

        {section === 'skills' && (
          <ErrorBoundary><SkillsPanel /></ErrorBoundary>
        )}

        {section === 'flows' && (
          <ErrorBoundary><WorkflowsPanel route={subRoute} onRoute={onFlowRoute} /></ErrorBoundary>
        )}

        {section === 'run' && (
          <ErrorBoundary><AgenticPanel agents={agents} /></ErrorBoundary>
        )}

        {section === 'approvals' && (
          <ErrorBoundary><ApprovalQueuePanel /></ErrorBoundary>
        )}

        {section === 'tools' && (
          <ErrorBoundary><QuarantinePanel /></ErrorBoundary>
        )}

        {section === 'reports' && (
          <div className="space-y-4">
            <ErrorBoundary><ExecutionAnalyticsPanel /></ErrorBoundary>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <ErrorBoundary><ReportsPanel /></ErrorBoundary>
              <div className="space-y-4">
                <ErrorBoundary><AIUsagePanel metrics={metrics} /></ErrorBoundary>
                <ErrorBoundary><MetricsPanel metrics={metrics} /></ErrorBoundary>
              </div>
            </div>
          </div>
        )}

        {section === 'fleet' && (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <ErrorBoundary><FleetPanel /></ErrorBoundary>
            <ErrorBoundary><HealthPanel /></ErrorBoundary>
          </div>
        )}

        {section === 'activity' && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div className="xl:col-span-2 space-y-4">
              <ErrorBoundary><AgentsPanel agents={agentList} /></ErrorBoundary>
              <ErrorBoundary><RecentEpisodesPanel /></ErrorBoundary>
              <ErrorBoundary><HistoryPanel /></ErrorBoundary>
            </div>
            <div className="space-y-4">
              <ErrorBoundary><AuditPanel seed={auditEvents} /></ErrorBoundary>
              <ErrorBoundary><ScheduledPanel refreshTick={scheduledTick} /></ErrorBoundary>
              <ErrorBoundary><MemoryPanel /></ErrorBoundary>
            </div>
          </div>
        )}

        {section === 'settings' && (
          <ErrorBoundary><SettingsPanel /></ErrorBoundary>
        )}
      </PanelShell>
    </>
  )
}
