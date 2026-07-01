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
                event: {
                  ts: 1711886500,
                  action: 'write_file',
                  target: '/tmp/new.txt',
                  source: 'agent',
                  status: 'blocked',
                  detail: 'blocked by policy',
                },
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
  }, snapshot)

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
    await route.fulfill({ json: snapshot.audit })
  })

  await page.route('**/api/scheduled', async (route) => {
    await route.fulfill({ json: [] })
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
  await page.getByRole('button', { name: /Fleet/ }).click()

  await expect(page.getByText('Runtime')).toBeVisible()
  await expect(page.getByText('openai / gpt-4o-mini')).toBeVisible()
  await expect(page.getByText('127.0.0.1:7766')).toBeVisible()

  await page.getByRole('button', { name: /Activity/ }).click()
  await expect(page.getByText('shell', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /Fleet/ }).click()

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

  await expect(page.getByText(/Import complete:/)).toBeVisible()
  await expect(page.getByText(/1 audit, 1 facts, 1 sessions, 1 tasks/)).toBeVisible()
})

test('dashboard updates when a live audit event arrives over websocket', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Activity/ }).click()

  await expect(page.getByText('pwd')).toBeVisible()
  await expect(page.getByText('/tmp/new.txt').first()).toBeVisible()
  await expect(page.getByText('write_file', { exact: true }).first()).toBeVisible()
})

test('export link points to backend snapshot endpoint', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Fleet/ }).click()

  const exportLink = page.getByRole('link', { name: 'Open JSON export' })
  await expect(exportLink).toHaveAttribute('href', '/api/export')
})
