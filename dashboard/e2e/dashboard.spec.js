import { test, expect } from '@playwright/test'

const snapshot = {
  type: 'snapshot',
  voice_active: false,
  metrics: {
    llm_calls: 3,
    tool_calls: 4,
    sessions: 5,
    agents_launched: 2,
    blocked_actions: 1,
  },
  audit: [
    {
      ts: 1711886400,
      action: 'shell',
      target: 'pwd',
      source: 'voice_tool',
      status: 'ok',
      detail: 'exit_code=0',
    },
  ],
  agents: [],
}

const liveAuditEvent = {
  ts: 1711886500,
  action: 'write_file',
  target: '/tmp/new.txt',
  source: 'agent',
  status: 'blocked',
  detail: 'blocked by policy',
}

const overview = {
  instance_id: 'inst_test',
  now_running: [
    {
      id: 'run123',
      name: 'ai-cli (codex)',
      cli: 'codex',
      task: 'Refactor the watcher',
      status: 'running',
      started_at: 1711886400,
    },
  ],
  pulse: { last_beat: '2026-03-31T12:00:00+00:00', mode: 'offline', alive: true },
  brains: { count: 4, admin: 'lhassa', standalone: false },
  skills: { installed: 3, recommended: 2 },
  approvals: {
    pending: 1,
    items: [
      {
        id: 'ap1',
        tipo: 'tool_graduation',
        accion: 'graduate',
        actor_id: 'duenio',
        summary: 'Graduate repo status action',
        creado_en: '2026-03-31T12:01:00+00:00',
      },
    ],
  },
  cost: {
    daily_token_budget: 10000,
    output_tokens_today: 1200,
    today_cost_usd: 0.004,
    kill_switch: false,
  },
  health: { ok: true, worst_level: 'ok' },
  tools: { count: 2, by_estado: { candidate: 1, graduada: 1 } },
  flows: { count: 2, last_status: 'done' },
  runs_today: { done: 1, stopped: 0, failed: 0 },
  activity: {
    recent: [
      {
        id: 'ep1',
        creado_en: '2026-03-31T12:02:00+00:00',
        despertar_por: 'heartbeat',
        decision: 'approval_required',
        resultado: 'Tool graduation needs approval',
      },
    ],
  },
  errors: {},
}

const scheduled = [
  {
    id: 'sched1',
    name: 'Check memory backup',
    run_at: '2099-01-01T00:00:00+00:00',
    message: 'Confirm backup freshness',
    status: 'pending',
  },
]

const tools = [
  {
    id: 'tool1',
    nombre: 'repo_status',
    dominio: 'git',
    estado: 'graduada',
    metrica_actual: 1,
    metrica_baseline: 1,
    usos: 4,
    fallos: 0,
  },
]

const skills = [
  { name: 'code-review', description: 'Review code.' },
  { name: 'documentation', description: 'Write docs.' },
]

const config = {
  groups: ['Heartbeat', 'Models', 'Budget & Safety', 'Memory', 'Surfaces', 'Org'],
  available: {
    heartbeat_providers: ['auto', 'local', 'cloud', 'opencode', 'claude', 'codex'],
    fallback_providers: ['opencode', 'claude', 'codex', 'local', 'default'],
    router_modes: ['rules', 'off'],
    model_options: ['claude-sonnet-4-6', 'claude-opus-4-8', 'llama-3.3-70b-versatile', 'llama3.2'],
    opencode_models: ['local-cli-fast'],
  },
  settings: [
    {
      key: 'heartbeat_provider',
      env_var: 'LHASSA_HEARTBEAT_PROVIDER',
      label: 'Heartbeat provider',
      group: 'Heartbeat',
      type: 'enum',
      value: 'auto',
      default: 'auto',
      overridden: false,
      secret: false,
      restart: 'none',
      options: ['auto', 'local', 'cloud', 'opencode', 'claude', 'codex'],
      help: 'Backend help for heartbeat provider.',
    },
    {
      key: 'fallback_providers',
      env_var: 'LHASSA_FALLBACK_PROVIDERS',
      label: 'Fallback provider chain',
      group: 'Heartbeat',
      type: 'multi',
      value: 'claude,codex,default',
      default: 'claude,codex,default',
      overridden: false,
      secret: false,
      restart: 'none',
      options: ['opencode', 'claude', 'codex', 'local', 'default'],
      help: 'Backend help for fallback chain.',
    },
    {
      key: 'llm_provider',
      env_var: 'LHASSA_LLM_PROVIDER',
      label: 'Chat provider',
      group: 'Models',
      type: 'enum',
      value: 'ollama',
      default: 'auto',
      overridden: true,
      secret: false,
      restart: 'none',
      options: ['ollama', 'openai', 'anthropic', 'groq', 'openrouter', 'cerebras', 'opencode', 'claude', 'codex'],
      help: 'Backend help for chat provider.',
    },
    {
      key: 'opencode_model',
      env_var: 'LHASSA_OPENCODE_MODEL',
      label: 'OpenCode model',
      group: 'Heartbeat',
      type: 'str',
      value: 'local-cli-fast',
      default: 'local-cli-fast',
      overridden: false,
      secret: false,
      restart: 'none',
      options: ['local-cli-fast'],
      allow_custom: true,
      help: 'Backend help for CLI model.',
    },
    {
      key: 'model',
      env_var: 'LHASSA_MODEL',
      label: 'Chat model',
      group: 'Models',
      type: 'str',
      value: 'llama3.2',
      default: '(provider default)',
      overridden: false,
      secret: false,
      restart: 'none',
      options: ['claude-sonnet-4-6', 'claude-opus-4-8', 'llama-3.3-70b-versatile', 'llama3.2'],
      allow_custom: true,
      help: 'Backend help for chat model.',
    },
    {
      key: 'llm_daily_token_budget',
      env_var: 'LHASSA_LLM_DAILY_TOKEN_BUDGET',
      label: 'Daily output-token budget',
      group: 'Budget & Safety',
      type: 'int',
      value: 0,
      default: 0,
      overridden: false,
      secret: false,
      restart: 'none',
      help: 'Backend help for budget.',
    },
  ],
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((payload) => {
    class MockWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      constructor(url) {
        this.url = url
        this.readyState = MockWebSocket.CONNECTING
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN
          this.onopen?.()
          this.onmessage?.({ data: JSON.stringify(payload) })
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({
                type: 'audit_event',
                event: payload.liveAuditEvent,
              }),
            })
          }, 300)
        }, 0)
      }

      close() {
        this.readyState = MockWebSocket.CLOSED
        this.onclose?.()
      }

      send() {}
      addEventListener() {}
      removeEventListener() {}
    }

    window.WebSocket = MockWebSocket
  }, { ...snapshot, liveAuditEvent })

  await page.route('**/api/health', async (route) => {
    await route.fulfill({
      json: {
        voice_active: false,
        uptime_seconds: 125,
        ws_clients: 1,
        agent_runs: 0,
        dashboard_built: true,
        llm_provider: 'openai',
        model: 'gpt-4o-mini',
        agent_backend: 'builtin',
        vision_provider: 'openai',
        stt_language: 'es',
        allow_destructive_shell: false,
        file_write_root: '/tmp/lhassa-root',
        api_host: '127.0.0.1',
        api_port: 7766,
      },
    })
  })

  await page.route('**/api/audit', async (route) => {
    await route.fulfill({ json: [liveAuditEvent, ...snapshot.audit] })
  })

  await page.route('**/api/overview', async (route) => {
    await route.fulfill({ json: overview })
  })

  await page.route('**/api/scheduled', async (route) => {
    await route.fulfill({ json: scheduled })
  })

  await page.route('**/api/tools', async (route) => {
    await route.fulfill({ json: tools })
  })

  await page.route('**/api/skills', async (route) => {
    await route.fulfill({ json: skills })
  })

  await page.route('**/api/code/clis', async (route) => {
    await route.fulfill({ json: { clis: ['codex', 'claude'], default: 'codex' } })
  })

  await page.route('**/api/config', async (route) => {
    if (route.request().method() === 'PATCH') {
      await route.fulfill({ json: { results: [], restart_hint: 'Applied live — no restart needed.', effective: config } })
      return
    }
    await route.fulfill({ json: config })
  })

  await page.route('**/api/approvals/ap1', async (route) => {
    await route.fulfill({ json: { ok: true, approval_id: 'ap1', approved: true } })
  })

  await page.route('**/api/memory/facts', async (route) => {
    await route.fulfill({ json: [{ key: 'name', value: 'Alice' }] })
  })

  await page.route('**/api/memory/sessions', async (route) => {
    await route.fulfill({ json: [{ date: '2026-03-31T00:00:00+00:00', summary: 'Imported session' }] })
  })

  await page.route('**/api/import?dry_run=1', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      json: {
        ok: true,
        dry_run: true,
        audit_events: body.audit?.length || 0,
        facts: body.facts?.length || 0,
        sessions: body.sessions?.length || 0,
        scheduled_tasks: body.scheduled_tasks?.length || 0,
      },
    })
  })

  await page.route('**/api/import', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      json: {
        ok: true,
        dry_run: false,
        audit_events: body.audit?.length || 0,
        facts: body.facts?.length || 0,
        sessions: body.sessions?.length || 0,
        scheduled_tasks: body.scheduled_tasks?.length || 0,
      },
    })
  })
})

test('runtime panel renders and imports a snapshot through dry-run flow', async ({ page }) => {
  page.on('dialog', (dialog) => dialog.accept())

  await page.goto('/')
  await page.getByRole('button', { name: 'Readiness', exact: true }).click()

  await expect(page.getByText('Runtime')).toBeVisible()
  await expect(page.getByText('openai / gpt-4o-mini')).toBeVisible()
  await expect(page.getByText('127.0.0.1:7766')).toBeVisible()

  await page.getByRole('button', { name: 'Activity', exact: true }).click()
  await expect(page.getByText('shell', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Readiness', exact: true }).click()

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({
    name: 'snapshot.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({
      audit: [{ ts: 1, action: 'shell', target: 'pwd', source: 'agent', status: 'ok', detail: '' }],
      facts: [{ key: 'name', value: 'Alice' }],
      sessions: [{ date: '2026-03-31T00:00:00+00:00', summary: 'Imported' }],
      scheduled_tasks: [{ name: 'Task', run_at: '2099-01-01T00:00:00+00:00', message: 'Ping', status: 'pending' }],
    })),
  })

  // Scope to the panel (role=main): the success message now also appears as a global
  // toast (role=status), so an unscoped locator matches two elements.
  await expect(page.getByRole('main').getByText(/Import complete:/)).toBeVisible()
  await expect(page.getByRole('main').getByText(/1 audit, 1 facts, 1 sessions, 1 tasks/)).toBeVisible()
})

test('dashboard updates when a live audit event arrives over websocket', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Activity', exact: true }).click()

  await expect(page.getByText('pwd')).toBeVisible()
  await expect(page.getByText('/tmp/new.txt').first()).toBeVisible()
  await expect(page.getByText('write_file', { exact: true }).first()).toBeVisible()
})

test('export link points to backend snapshot endpoint', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Readiness/ }).click()

  const exportLink = page.getByRole('link', { name: 'Open JSON export' })
  await expect(exportLink).toHaveAttribute('href', '/api/export')
})

test('inbox, watchlist and capabilities expose operational work queues', async ({ page }) => {
  await page.goto('/')

  await page.getByRole('button', { name: /Inbox/ }).click()
  await expect(page.getByRole('heading', { name: 'What needs a decision' })).toBeVisible()
  await expect(page.getByText('Graduate repo status action')).toBeVisible()
  await expect(page.getByText('Refactor the watcher')).toBeVisible()
  await expect(page.getByText('pwd')).toBeVisible()

  await page.getByRole('button', { name: /Watchlist/ }).click()
  await expect(page.getByRole('heading', { name: 'Active watchers and due checks' })).toBeVisible()
  await expect(page.getByText('Check memory backup')).toBeVisible()
  await expect(page.getByText('Confirm backup freshness')).toBeVisible()

  await page.getByRole('button', { name: /Capabilities/ }).click()
  await expect(page.getByRole('heading', { name: 'What Lhassa can safely do' })).toBeVisible()
  await expect(page.getByText('Delegate work to local CLIs')).toBeVisible()
  await expect(page.getByText('repo_status')).toBeVisible()
  await expect(page.getByText('2 installed').first()).toBeVisible()
})

test('settings is provider-agnostic by default and reveals raw details in advanced mode', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Settings/ }).click()

  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByText('Autonomy engine', { exact: true })).toBeVisible()
  await expect(page.getByText('Primary interaction engine', { exact: true })).toBeVisible()
  await expect(page.getByText('Primary model preset', { exact: true })).toBeVisible()
  await expect(page.getByText('Effective choice: Local server: Ollama default -> llama3.2')).toBeVisible()
  const primaryModelSelect = page.locator('select').filter({
    has: page.locator('option', { hasText: 'Use Local server: Ollama default' }),
  })
  await expect(primaryModelSelect).toBeVisible()
  await expect(primaryModelSelect).toHaveValue('__default__')
  await expect(page.getByText('Basic mode shows operational choices')).toBeVisible()
  await expect(page.getByText('opencode_model')).toHaveCount(0)

  await page.getByRole('button', { name: 'Advanced' }).click()
  await expect(page.getByText('Routine local-CLI preset')).toBeVisible()
  await expect(page.getByText('opencode_model', { exact: true })).toBeVisible()
  await expect(page.getByText('env LHASSA_OPENCODE_MODEL')).toBeVisible()
})

test('nav hides the sections whose capability pack is absent', async ({ page }) => {
  await page.route('**/api/packs', async (route) => {
    await route.fulfill({ json: { packs: [{ name: 'api', summary: 'Dashboard + API' }, { name: 'cv', summary: 'CV' }] } })
  })
  await page.goto('/')

  await expect(page.getByRole('button', { name: /Postulaciones/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /^Research$/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Calendar$/ })).toHaveCount(0)
  // Core sections never depend on a pack.
  await expect(page.getByRole('button', { name: /Settings/ }).first()).toBeVisible()
})
