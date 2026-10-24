import { useCallback, useEffect, useMemo, useState } from 'react'
import { copyTextToClipboard } from './clipboard.js'
import './App.css'

const PAGE_META = {
  overview: { title: 'Overview', description: 'A live view of gateway readiness, available tools, and upstream health.' },
  servers: { title: 'Servers & Tools', description: 'Discover available MCP tools, inspect their inputs, and manage upstream registrations.' },
  chat: { title: 'Chat Playground', description: 'Use the authenticated MCP assistant to explore and operate available tools.' },
  users: { title: 'Users & Access', description: 'Manage teams, identities, API keys, and tool permissions.' },
  audit: { title: 'Audit & Observability', description: 'Review recent authenticated activity, outcomes, and execution durations.' },
}

const PUBLIC_NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'servers', label: 'Servers & Tools' },
  { id: 'chat', label: 'Chat Playground' },
]

const ADMIN_NAV = [
  { id: 'users', label: 'Users & Access' },
  { id: 'audit', label: 'Audit & Observability' },
]

const EMPTY_ADMIN_DATA = { teams: [], identities: [], keys: [], permissions: [], audit: [], upstreams: [], metrics: null }
const UPSTREAM_FAILURE_LABELS = {
  timeout: 'Health check timed out',
  connection_error: 'Connection failed',
  discovery_error: 'Tool discovery failed',
}

const toolId = (tool) => JSON.stringify([tool.server, tool.name])

const EXAMPLES = {
  list_products: {},
  create_product: { name: 'Demo Product', regular_price: '10.00', description: 'Created from MCPHub dashboard' },
  list_orders: {},
  get_order: { order_id: 16 },
  update_order_status: { order_id: 16, status: 'processing' },
  list_inventory: {},
  update_stock: { sku: 'SKU-HEADPHONES', quantity: 25 },
  list_tickets: {},
  get_ticket: { ticket_id: 'TKT-1001' },
  create_ticket: {
    customer: 'Demo Customer',
    subject: 'Dashboard test ticket',
    description: 'Created from the MCPHub dashboard',
    priority: 'normal',
  },
  update_ticket_status: { ticket_id: 'TKT-1001', status: 'resolved' },
}

async function readApiJson(response, operation, { allowErrorStatus = false } = {}) {
  const contentType = response.headers.get('content-type') || ''
  const body = await response.text()
  const isJson = /(?:^|\s)application\/(?:[\w.-]+\+)?json(?:\s*;|\s*$)/i.test(contentType)
  let data

  if (body.trim() && isJson) {
    try {
      data = JSON.parse(body)
    } catch {
      // Report a safe status/content-type diagnostic below without echoing the body.
    }
  }

  if (!response.ok && !allowErrorStatus) {
    const detail = typeof data?.detail === 'string' ? data.detail : ''
    const responseKind = !body.trim() ? 'empty response' : !isJson || data === undefined ? 'non-JSON or invalid JSON response' : ''
    throw new Error(detail || `${operation} failed with HTTP ${response.status}${responseKind ? ` (${responseKind}${contentType ? `, ${contentType}` : ''})` : ''}.`)
  }

  if (!body.trim()) throw new Error(`${operation} returned an empty response (HTTP ${response.status}).`)
  if (!isJson || data === undefined) {
    throw new Error(`${operation} returned a non-JSON or invalid JSON response (HTTP ${response.status}${contentType ? `, ${contentType}` : ''}).`)
  }
  return data
}

function StatusBadge({ status }) {
  const label = status || 'Unknown'
  const className = label.toLowerCase().replace(/[^a-z]+/g, '-')
  return <span className={`status-badge status-${className}`}>{label}</span>
}

function MetricCard({ label, value, detail, tone = 'neutral' }) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">{detail}</div>
    </article>
  )
}

function UpstreamCard({ upstream, tools }) {
  const reason = UPSTREAM_FAILURE_LABELS[upstream.reason]
  return (
    <article className="server-card" key={upstream.name}>
      <div className="server-avatar" aria-hidden="true">{upstream.name.slice(0, 1).toUpperCase()}</div>
      <div className="server-info">
        <strong>{upstream.name}</strong>
        <span>{tools.filter((tool) => tool.server === upstream.name).length} authorized tools</span>
        {reason && <small className="upstream-reason">{reason}</small>}
      </div>
      <StatusBadge status={upstream.status} />
    </article>
  )
}

function ToolCallMetrics({ metrics, error }) {
  if (error) return <p className="inline-notice warning-notice" role="status">Tool-call metrics are unavailable.</p>
  if (!metrics) return null

  const totals = metrics.totals || {}
  const tools = Array.isArray(metrics.tools) ? metrics.tools : []
  return (
    <section className="surface-card tool-call-metrics">
      <div className="section-heading">
        <div><h2>Tool-call metrics</h2><p>Aggregated audit data from the last {metrics.window_hours} hours.</p></div>
      </div>
      <div className="metric-grid metrics-summary">
        <MetricCard label="Calls" value={totals.total_calls ?? 0} detail={`${totals.success_count ?? 0} succeeded · ${totals.denied_count ?? 0} denied`} />
        <MetricCard label="Success rate" value={`${totals.success_rate_percent ?? 0}%`} detail={`${totals.success_count ?? 0} successful calls`} tone="success" />
        <MetricCard label="Failed calls" value={totals.failure_count ?? 0} detail={`${totals.failure_rate_percent ?? 0}% of calls · ${totals.timeout_count ?? 0} timed out`} tone={totals.failure_count ? 'danger' : 'neutral'} />
        <MetricCard label="Average latency" value={totals.avg_duration_ms == null ? '—' : `${totals.avg_duration_ms} ms`} detail={`Maximum ${totals.max_duration_ms == null ? '—' : `${totals.max_duration_ms} ms`}`} tone="accent" />
      </div>
      <div className="table-wrap">
        <table><thead><tr><th>Upstream</th><th>Tool</th><th>Calls</th><th>Success</th><th>Failed</th><th>Denied</th><th>Average latency</th><th>Failure categories (timeout / tool / other)</th></tr></thead>
          <tbody>{tools.map((item) => <tr key={`${item.server_name}-${item.tool_name}`}>
            <td>{item.server_name}</td><td><code>{item.tool_name}</code></td><td>{item.total_calls}</td>
            <td>{item.success_count} ({item.success_rate_percent}%)</td><td>{item.failure_count}</td><td>{item.denied_count}</td>
            <td>{item.avg_duration_ms == null ? '—' : `${item.avg_duration_ms} ms`}</td><td>{item.timeout_count} / {item.tool_error_count} / {item.other_failure_count}</td>
          </tr>)}</tbody>
        </table>
        {!tools.length && <div className="empty-state">No attributed tool calls in this time window.</div>}
      </div>
      {metrics.truncated && <p className="muted-note">Showing the 200 most-called upstream/tool pairs.</p>}
    </section>
  )
}

function PageTitle({ eyebrow, title, description, children }) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  )
}

function App() {
  const [apiKey, setApiKey] = useState('')
  const [keyInput, setKeyInput] = useState('')
  const [identity, setIdentity] = useState(null)
  const [section, setSection] = useState('overview')
  const [adminTab, setAdminTab] = useState('identities')
  const [adminData, setAdminData] = useState(EMPTY_ADMIN_DATA)
  const [metricsError, setMetricsError] = useState(false)
  const [adminForm, setAdminForm] = useState({
    teamSlug: '', teamName: '', teamId: '', identityName: '', role: 'viewer', identityType: 'user',
    keyIdentity: '', subjectType: 'team', subjectId: '', server: '', tool: '*',
    newUpstreamName: '', newUpstreamUrl: '', editUpstreamId: '', editUpstreamName: '', editUpstreamUrl: '',
  })
  const [oneTimeKey, setOneTimeKey] = useState('')
  const [copyFeedback, setCopyFeedback] = useState('')
  const [tools, setTools] = useState([])
  const [upstreamStatuses, setUpstreamStatuses] = useState([])
  const [upstreamCheckedAt, setUpstreamCheckedAt] = useState(null)
  const [upstreamStatusError, setUpstreamStatusError] = useState('')
  const [gatewayHealth, setGatewayHealth] = useState({ health: null, readiness: null, checkedAt: null })
  const [selectedTool, setSelectedTool] = useState('')
  const [serverFilter, setServerFilter] = useState('all')
  const [argumentsText, setArgumentsText] = useState('{}')
  const [result, setResult] = useState(null)
  const [loadingTools, setLoadingTools] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState([])
  const [chatBusy, setChatBusy] = useState(false)
  const [auditFilter, setAuditFilter] = useState({ query: '', outcome: 'all' })

  const apiRequest = useCallback(async (path, options = {}) => {
    const response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        'X-API-Key': apiKey,
        ...options.headers,
      },
    })
    if (response.status === 401) {
      setApiKey('')
      setIdentity(null)
      setOneTimeKey('')
      setCopyFeedback('')
      setTools([])
      setUpstreamStatuses([])
      setAdminData(EMPTY_ADMIN_DATA)
      setChatMessages([])
      setGatewayHealth({ health: null, readiness: null, checkedAt: null })
      setError('API key was rejected or revoked. Sign in with a valid key.')
    }
    return response
  }, [apiKey])

  const loadGatewayHealth = useCallback(async () => {
    if (!apiKey) return
    const readStatus = async (path) => {
      try {
        const response = await apiRequest(path)
        const data = await readApiJson(response, 'Gateway health check')
        return response.ok && typeof data.status === 'string' ? data.status : 'Unavailable'
      } catch {
        return 'Unavailable'
      }
    }
    const [health, readiness] = await Promise.all([readStatus('/health'), readStatus('/ready')])
    setGatewayHealth({ health, readiness, checkedAt: new Date().toISOString() })
  }, [apiKey, apiRequest])

  const loadTools = useCallback(async () => {
    if (!apiKey) return
    setLoadingTools(true)
    setError('')
    try {
      const response = await apiRequest('/tools')
      const data = await readApiJson(response, 'Tool discovery')
      if (!Array.isArray(data)) throw new Error('Unexpected tool-list response')
      setTools(data)
      setSelectedTool((current) => data.some((tool) => toolId(tool) === current) ? current : data[0] ? toolId(data[0]) : '')
    } catch (err) {
      setError(`${err.message}. Check the gateway connection and retry.`)
    } finally {
      setLoadingTools(false)
    }
  }, [apiKey, apiRequest])

  const loadUpstreamStatuses = useCallback(async () => {
    if (!apiKey) return
    try {
      const response = await apiRequest('/upstreams/status')
      const data = await readApiJson(response, 'Upstream status check')
      if (!Array.isArray(data)) throw new Error('Unexpected upstream status response')
      setUpstreamStatuses(data)
      setUpstreamCheckedAt(new Date().toISOString())
      setUpstreamStatusError('')
    } catch (err) {
      setUpstreamStatusError(err.message)
      // Keep the last successful snapshot; indicate that it may be stale and retry on the next poll.
    }
  }, [apiKey, apiRequest])

  useEffect(() => {
    if (!apiKey) return undefined
    const refresh = window.setTimeout(() => loadTools(), 0)
    return () => window.clearTimeout(refresh)
  }, [apiKey, loadTools])

  useEffect(() => {
    if (!apiKey) return undefined
    const refresh = window.setTimeout(() => {
      loadGatewayHealth()
      loadUpstreamStatuses()
    }, 0)
    const interval = window.setInterval(() => {
      loadGatewayHealth()
      loadUpstreamStatuses()
    }, 15000)
    return () => {
      window.clearTimeout(refresh)
      window.clearInterval(interval)
    }
  }, [apiKey, loadGatewayHealth, loadUpstreamStatuses])

  useEffect(() => {
    if (!apiKey) return undefined
    let active = true
    const refresh = window.setTimeout(async () => {
      try {
        const response = await apiRequest('/whoami')
        const data = await readApiJson(response, 'Identity check')
        if (active) setIdentity(data)
      } catch (err) {
        if (!active) return
        setIdentity(null)
        if (/HTTP 401\b/.test(err.message)) {
          setApiKey('')
          setOneTimeKey('')
          setUpstreamStatuses([])
          setError('API key was rejected or revoked. Sign in with a valid key.')
        } else {
          setError(err.message)
        }
      }
    }, 0)
    return () => {
      active = false
      window.clearTimeout(refresh)
    }
  }, [apiKey, apiRequest])

  const loadAdminData = useCallback(async () => {
    const paths = ['/admin/teams', '/admin/identities', '/admin/keys', '/admin/permissions', '/admin/audit?limit=100&offset=0', '/admin/upstreams']
    const responses = await Promise.all(paths.map((path) => apiRequest(path)))
    const values = await Promise.all(responses.map((response) => readApiJson(response, 'Admin request')))
    let metrics = null
    let failedToLoadMetrics = false
    try {
      const response = await apiRequest('/admin/observability/tool-calls?hours=24')
      metrics = await readApiJson(response, 'Tool-call metrics')
    } catch {
      failedToLoadMetrics = true
    }
    setMetricsError(failedToLoadMetrics)
    setAdminData({ teams: values[0], identities: values[1], keys: values[2], permissions: values[3], audit: values[4], upstreams: values[5], metrics })
  }, [apiRequest])

  useEffect(() => {
    if (identity?.role === 'admin' && ['overview', 'servers', 'users', 'audit'].includes(section)) {
      const refresh = window.setTimeout(() => loadAdminData().catch((err) => setError(err.message)), 0)
      return () => window.clearTimeout(refresh)
    }
    return undefined
  }, [identity, section, loadAdminData])

  const gatewayOnline = gatewayHealth.health === 'healthy' && gatewayHealth.readiness === 'ready'

  async function signIn(event) {
    event.preventDefault()
    setError('')
    const candidate = keyInput.trim()
    if (!candidate) return
    try {
      const response = await fetch('/api/whoami', { headers: { 'X-API-Key': candidate } })
      const data = await readApiJson(response, 'Sign-in')
      setIdentity(data)
      setApiKey(candidate)
      setKeyInput('')
    } catch (err) {
      setError(err.message)
    }
  }

  function signOut() {
    setApiKey('')
    setIdentity(null)
    setGatewayHealth({ health: null, readiness: null, checkedAt: null })
    setUpstreamStatuses([])
    setUpstreamCheckedAt(null)
    setTools([])
    setAdminData(EMPTY_ADMIN_DATA)
    setOneTimeKey('')
    setCopyFeedback('')
    setChatMessages([])
    setResult(null)
    setError('')
    setSection('overview')
  }

  async function submitAdmin(event, action) {
    event.preventDefault()
    setError('')
    try {
      await action()
      await loadAdminData()
    } catch (err) {
      setError(err.message)
    }
  }

  async function copyOneTimeKey() {
    setCopyFeedback('')
    try {
      await copyTextToClipboard(oneTimeKey)
      setCopyFeedback('API key copied to clipboard.')
    } catch {
      setCopyFeedback('Could not copy the key. Select it above and copy it manually.')
    }
  }

  function setForm(field, value) {
    setAdminForm((current) => ({ ...current, [field]: value }))
  }

  const serverNames = useMemo(() => [...new Set([
    ...upstreamStatuses.map((server) => server.name),
    ...adminData.upstreams.map((server) => server.name),
    ...tools.map((tool) => tool.server),
  ])].sort(), [adminData.upstreams, tools, upstreamStatuses])

  const visibleTools = useMemo(
    () => tools.filter((tool) => serverFilter === 'all' || tool.server === serverFilter),
    [tools, serverFilter],
  )
  const activeTool = tools.find((tool) => toolId(tool) === selectedTool)
  const activeToolIsAmbiguous = activeTool && tools.filter((tool) => tool.name === activeTool.name).length > 1
  const connectedCount = upstreamStatuses.filter((server) => server.status === 'Connected').length
  const failedEvents = adminData.audit.filter((event) => event.outcome !== 'success')
  const identityNamesById = Object.fromEntries(adminData.identities.map((record) => [record.id, record.name]))
  const filteredAudit = adminData.audit.filter((event) => {
    const query = auditFilter.query.trim().toLowerCase()
    const identityName = identityNamesById[event.identity_id] || `identity ${event.identity_id || ''}`
    const searchable = [identityName, event.server_name, event.tool_name, event.event_type, event.outcome, event.error_class].filter(Boolean).join(' ').toLowerCase()
    return (!query || searchable.includes(query)) && (auditFilter.outcome === 'all' || event.outcome === auditFilter.outcome)
  })
  const page = PAGE_META[section] || PAGE_META.overview
  const nav = identity?.role === 'admin' ? [...PUBLIC_NAV, ...ADMIN_NAV] : PUBLIC_NAV
  const refreshCurrentPage = () => {
    loadGatewayHealth()
    loadUpstreamStatuses()
    if (section === 'servers') loadTools()
    if (identity?.role === 'admin' && ['overview', 'servers', 'users', 'audit'].includes(section)) loadAdminData().catch((err) => setError(err.message))
  }

  if (!apiKey || !identity) return (
    <main className="app-shell login-shell">
      <form className="login-card" onSubmit={signIn}>
        <div className="brand-mark" aria-hidden="true">M</div>
        <div className="eyebrow">MCPHUB ACCESS</div>
        <h1>Sign in to your workspace</h1>
        <p>Use an MCPHub API key. It stays in this tab’s memory and is cleared when you sign out or close the page.</p>
        {error && <div className="alert error-alert" role="alert">{error}</div>}
        <label className="field-label" htmlFor="api-key">API key</label>
        <input id="api-key" className="text-input" type="password" autoComplete="off" value={keyInput} onChange={(event) => setKeyInput(event.target.value)} />
        <button className="primary-button" type="submit">Connect securely</button>
      </form>
    </main>
  )

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">M</div>
          <div><strong>MCPHub</strong><span>Developer platform</span></div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="primary-nav" aria-label="Primary navigation">
          {nav.map((item) => (
            <button key={item.id} className={`nav-item ${section === item.id ? 'active' : ''}`} aria-current={section === item.id ? 'page' : undefined} onClick={() => setSection(item.id)}>
              <span className="nav-marker" aria-hidden="true">{item.label.slice(0, 1)}</span>
              <span>{item.label}</span>
              {item.id === 'servers' && <span className="nav-count">{tools.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className={`status-dot ${gatewayOnline ? 'online' : ''}`} />
          <div><strong>{gatewayOnline ? 'Gateway ready' : 'Checking gateway'}</strong><span>Live status</span></div>
        </div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> {page.title}</div>
          <div className="header-actions">
            <div className="identity-chip"><span className="identity-avatar" aria-hidden="true">{identity.name.slice(0, 1).toUpperCase()}</span><span><strong>{identity.name}</strong><small>{identity.role}</small></span></div>
            <button className="secondary-button" onClick={signOut}>Sign out</button>
          </div>
        </header>

        <div className="page-content">
          <PageTitle eyebrow="MCPHUB / OPERATIONS" title={page.title} description={page.description}>
            <StatusBadge status={gatewayOnline ? 'Ready' : 'Unavailable'} />
            <button className="secondary-button" onClick={refreshCurrentPage}>Refresh</button>
          </PageTitle>

          {error && <div className="alert error-alert" role="alert"><strong>Request issue</strong><span>{error}</span></div>}

          {section === 'overview' && <section className="page-section" aria-label="Overview">
            <div className="metric-grid">
              <MetricCard label="Gateway readiness" value={gatewayHealth.readiness === 'ready' ? 'Ready' : gatewayHealth.readiness === 'Unavailable' ? 'Unavailable' : 'Checking'} detail={`Liveness: ${gatewayHealth.health || 'checking'}`} tone={gatewayOnline ? 'success' : 'neutral'} />
              <MetricCard label="Authorized tools" value={loadingTools ? '—' : tools.length} detail="Visible to this API-key identity" tone="accent" />
              <MetricCard label="Connected upstreams" value={`${connectedCount} / ${upstreamStatuses.length || '—'}`} detail="Health checked independently" tone={connectedCount ? 'success' : 'neutral'} />
              {identity.role === 'admin' && <MetricCard label="Recent failures" value={failedEvents.length} detail="In the latest 100 audit records" tone={failedEvents.length ? 'danger' : 'neutral'} />}
            </div>

            <div className="section-heading"><div><h2>Upstream health</h2><p>Each registered MCP server is checked separately.</p></div><span className="muted-note">{upstreamCheckedAt ? `Updated ${new Date(upstreamCheckedAt).toLocaleTimeString()}` : 'Waiting for first check'}</span></div>
            {upstreamStatusError && <p className="inline-notice warning-notice">Latest status check failed; displayed statuses may be stale.</p>}
            <div className="server-grid">
              {upstreamStatuses.map((upstream) => <UpstreamCard key={upstream.name} upstream={upstream} tools={tools} />)}
              {!upstreamStatuses.length && <div className="empty-state">Upstream status is not available yet.</div>}
            </div>

            {identity.role === 'admin' && <ToolCallMetrics metrics={adminData.metrics} error={metricsError} />}

            {identity.role === 'admin' && <section className="surface-card recent-failures">
              <div className="section-heading"><div><h2>Recent failures</h2><p>Latest unsuccessful events from the currently loaded audit page.</p></div><button className="text-button" onClick={() => setSection('audit')}>Open audit log</button></div>
              {failedEvents.length ? <div className="table-wrap"><table><thead><tr><th>Time</th><th>Identity</th><th>Server / tool</th><th>Outcome</th><th>Duration</th></tr></thead><tbody>
                {failedEvents.slice(0, 6).map((event) => <tr key={event.id}><td>{event.created_at}</td><td>{identityNamesById[event.identity_id] || `Identity ${event.identity_id || '—'}`}</td><td>{event.server_name || '—'} / {event.tool_name || event.event_type}</td><td><StatusBadge status={event.outcome} /></td><td>{event.duration_ms == null ? '—' : `${event.duration_ms} ms`}</td></tr>)}
              </tbody></table></div> : <div className="empty-state compact-empty">No failures in the latest 100 audit records.</div>}
            </section>}
          </section>}

          {section === 'servers' && <section className="page-section">
            <div className="section-heading"><div><h2>Registered upstreams</h2><p>Status and tool counts for every upstream currently known to the gateway.</p></div></div>
            {upstreamStatusError && <p className="inline-notice warning-notice">Latest status check failed; displayed statuses may be stale.</p>}
            <div className="server-grid">
              {upstreamStatuses.map((upstream) => <UpstreamCard key={upstream.name} upstream={upstream} tools={tools} />)}
              {!upstreamStatuses.length && <div className="empty-state">No upstream status has been loaded.</div>}
            </div>

            {identity.role === 'admin' && <section className="surface-card management-card">
              <div className="section-heading"><div><h2>Upstream management</h2><p>Endpoint addresses are not returned or displayed from saved registrations.</p></div></div>
              <div className="form-grid">
                <form className="form-card" onSubmit={(event) => submitAdmin(event, async () => {
                  const response = await apiRequest('/admin/upstreams', { method: 'POST', body: JSON.stringify({ name: adminForm.newUpstreamName, endpoint_url: adminForm.newUpstreamUrl, enabled: true }) })
                  await readApiJson(response, 'Add upstream')
                  setForm('newUpstreamName', ''); setForm('newUpstreamUrl', '')
                  await loadTools()
                })}>
                  <h3>Add upstream</h3><label className="field-label" htmlFor="new-upstream-name">Server name</label><input id="new-upstream-name" className="text-input" placeholder="server-name" value={adminForm.newUpstreamName} onChange={(event) => setForm('newUpstreamName', event.target.value)} required /><label className="field-label" htmlFor="new-upstream-url">Streamable HTTP URL</label><input id="new-upstream-url" className="text-input" type="url" placeholder="https://service.example/mcp" value={adminForm.newUpstreamUrl} onChange={(event) => setForm('newUpstreamUrl', event.target.value)} required /><button className="primary-button">Add upstream</button>
                </form>
                <form className="form-card" onSubmit={(event) => submitAdmin(event, async () => {
                  const response = await apiRequest(`/admin/upstreams/${adminForm.editUpstreamId}`, { method: 'PUT', body: JSON.stringify({ name: adminForm.editUpstreamName, endpoint_url: adminForm.editUpstreamUrl }) })
                  await readApiJson(response, 'Edit upstream')
                  setForm('editUpstreamId', ''); setForm('editUpstreamName', ''); setForm('editUpstreamUrl', '')
                  await loadTools()
                })}>
                  <h3>Edit upstream</h3><label className="field-label" htmlFor="edit-upstream-id">Registered server</label><select id="edit-upstream-id" className="text-input" value={adminForm.editUpstreamId} onChange={(event) => { const selected = adminData.upstreams.find((item) => String(item.id) === event.target.value); setForm('editUpstreamId', event.target.value); setForm('editUpstreamName', selected?.name || '') }} required><option value="">Select upstream</option>{adminData.upstreams.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><label className="field-label" htmlFor="edit-upstream-name">New server name</label><input id="edit-upstream-name" className="text-input" value={adminForm.editUpstreamName} onChange={(event) => setForm('editUpstreamName', event.target.value)} required /><label className="field-label" htmlFor="edit-upstream-url">New Streamable HTTP URL</label><input id="edit-upstream-url" className="text-input" type="url" placeholder="https://service.example/mcp" value={adminForm.editUpstreamUrl} onChange={(event) => setForm('editUpstreamUrl', event.target.value)} required /><button className="primary-button">Save upstream</button>
                </form>
              </div>
              <div className="table-wrap"><table><thead><tr><th>Server</th><th>Enabled state</th><th>Connection</th><th>Actions</th></tr></thead><tbody>{adminData.upstreams.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.enabled ? 'Enabled' : 'Disabled'}</td><td><StatusBadge status={upstreamStatuses.find((status) => status.name === item.name)?.status || (item.enabled ? 'Unknown' : 'Disabled')} /></td><td className="row-actions"><button className="secondary-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/upstreams/${item.id}/enabled`, { method: 'PATCH', body: JSON.stringify({ enabled: !item.enabled }) }); await readApiJson(response, 'Change upstream status'); await loadTools() })}>{item.enabled ? 'Disable' : 'Enable'}</button><button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/upstreams/${item.id}`, { method: 'DELETE' }); await readApiJson(response, 'Remove upstream'); await loadTools() })}>Remove</button></td></tr>)}</tbody></table></div>
            </section>}

            <section className="surface-card tool-explorer">
              <div className="section-heading"><div><h2>Tool explorer</h2><p>Select an authorized tool, provide its arguments, and inspect the structured response.</p></div><span className="count-pill">{tools.length} tools</span></div>
              <div className="explorer-layout">
                <div className="tool-list-panel">
                  <div className="tool-list-header"><strong>Available tools</strong><select value={serverFilter} onChange={(event) => setServerFilter(event.target.value)} aria-label="Filter tools by server"><option value="all">All servers</option>{serverNames.map((name) => <option value={name} key={name}>{name}</option>)}</select></div>
                  <div className="tool-list">
                    {loadingTools && <div className="empty-state">Discovering tools…</div>}
                    {!loadingTools && visibleTools.map((tool) => <button key={toolId(tool)} className={`tool-item ${selectedTool === toolId(tool) ? 'selected' : ''}`} onClick={() => { setSelectedTool(toolId(tool)); setArgumentsText(JSON.stringify(EXAMPLES[tool.name] ?? {}, null, 2)); setResult(null); setError('') }}><span className="tool-marker" aria-hidden="true">MCP</span><span className="tool-item-text"><strong>{tool.name}</strong><small>{tool.server}</small></span><span className="tool-chevron" aria-hidden="true">›</span></button>)}
                    {!loadingTools && visibleTools.length === 0 && <div className="empty-state">No tools found for this filter.</div>}
                  </div>
                </div>
                <div className="tool-workspace">
                  {activeTool ? <>
                    <div className="active-tool-heading"><div><div className="tool-kicker">SELECTED TOOL</div><h3>{activeTool.name}</h3><p>{activeTool.description || 'No description provided.'}</p></div><span className="server-tag">{activeTool.server}</span></div>
                    {activeToolIsAmbiguous && <p className="inline-notice warning-notice">Another upstream exposes this tool name. The current gateway call contract routes by tool name only, so this call cannot be selected unambiguously.</p>}
                    <form onSubmit={async (event) => {
                      event.preventDefault(); setError(''); setResult(null)
                      let args
                      try { args = JSON.parse(argumentsText); if (!args || Array.isArray(args) || typeof args !== 'object') throw new Error('Arguments must be a JSON object.') } catch (err) { setError(`Invalid JSON: ${err.message}`); return }
                      setRunning(true)
                      try {
                        const response = await apiRequest('/call', { method: 'POST', body: JSON.stringify({ tool: activeTool.name, arguments: args }) })
                        const data = await readApiJson(response, 'Tool call', { allowErrorStatus: true })
                        setResult(data)
                        if (!response.ok) setError(data.detail || data.error || `HTTP ${response.status}`)
                        else if (data.is_error) setError('The MCP tool returned an error.')
                      } catch (err) { setError(`Request failed: ${err.message}`) } finally { setRunning(false) }
                    }}>
                      <label className="field-label" htmlFor="tool-arguments">Arguments <span>JSON</span></label><textarea id="tool-arguments" className="json-editor" value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} spellCheck="false" rows={10} />
                      <div className="form-footer"><span>Arguments are sent through the authenticated gateway.</span><button className="primary-button" type="submit" disabled={running || !gatewayOnline || !activeTool || activeToolIsAmbiguous}>{running ? 'Running…' : 'Run tool'}</button></div>
                    </form>
                    {(result || error) && <div className="result-panel"><div className="result-header"><strong>Execution result</strong>{result && <StatusBadge status={result.is_error || error ? 'Failed' : 'Succeeded'} />}</div>{error && <div className="result-error" role="alert">{error}</div>}{result && <pre>{JSON.stringify(result, null, 2)}</pre>}</div>}
                  </> : <div className="empty-workspace"><div className="empty-symbol" aria-hidden="true">MCP</div><h3>{loadingTools ? 'Discovering tools' : 'Select a tool'}</h3><p>{loadingTools ? 'Connecting to your MCP gateway.' : 'Choose a tool from the list to configure and execute it.'}</p>{!loadingTools && !gatewayOnline && <button className="secondary-button" onClick={loadTools}>Retry tool discovery</button>}</div>}
                </div>
              </div>
            </section>
          </section>}

          {section === 'chat' && <section className="page-section">
            <div className="surface-card assistant-card">
              <div className="assistant-context"><span className="assistant-avatar" aria-hidden="true">AI</span><div><strong>MCPHub assistant</strong><p>Answers with tools available to your authenticated identity.</p></div></div>
              <div className="assistant-messages" aria-live="polite">
                {chatMessages.length === 0 && <div className="assistant-welcome"><span className="welcome-kicker">CHAT PLAYGROUND</span><h2>What would you like to do?</h2><p>Ask a question about the MCP tools available to you. The assistant shows which tools it calls and their outcomes.</p><div className="suggestion-list"><span>“What can I ask you?”</span><span>“Show me today’s orders”</span><span>“Check current inventory”</span></div></div>}
                {chatMessages.map((message, index) => <article className={`assistant-message ${message.role}`} key={`${index}-${message.role}`}><strong>{message.role === 'user' ? 'You' : 'MCPHub assistant'}</strong><span>{message.text}</span>{message.toolCalls?.length > 0 && <ul className="assistant-tool-calls">{message.toolCalls.map((call, callIndex) => <li key={`${call.name}-${callIndex}`}><code>{call.name}</code><StatusBadge status={call.outcome} /></li>)}</ul>}</article>)}
                {chatBusy && <div className="assistant-hint">Working through the gateway…</div>}
              </div>
              <form className="assistant-form" onSubmit={async (event) => {
                event.preventDefault(); const prompt = chatInput.trim(); if (!prompt || chatBusy) return
                setChatInput(''); setChatMessages((messages) => [...messages, { role: 'user', text: prompt }]); setChatBusy(true)
                try {
                  const messages = [...chatMessages.map((message) => ({ role: message.role, content: message.text })), { role: 'user', content: prompt }].slice(-20)
                  const response = await apiRequest('/assistant', { method: 'POST', body: JSON.stringify({ messages }) })
                  const data = await readApiJson(response, 'Assistant request')
                  setChatMessages((current) => [...current, { role: 'assistant', text: data.answer || 'The assistant returned no answer.', toolCalls: Array.isArray(data.tool_calls) ? data.tool_calls : [] }])
                } catch (err) { setChatMessages((messages) => [...messages, { role: 'assistant', text: `Could not complete request: ${err.message}` }]) } finally { setChatBusy(false) }
              }}>
                <label className="sr-only" htmlFor="chat-message">Message MCPHub assistant</label><input id="chat-message" className="text-input" value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="Ask about products, orders, inventory, or support" disabled={chatBusy || !gatewayOnline} /><button className="primary-button" type="submit" disabled={chatBusy || !gatewayOnline || !chatInput.trim()}>{chatBusy ? 'Working…' : 'Send message'}</button>
              </form>
            </div>
          </section>}

          {section === 'users' && identity.role === 'admin' && <section className="page-section">
            <div className="tabs" role="tablist" aria-label="User and access management">
              {[['identities', 'Teams & identities'], ['keys', 'API keys'], ['permissions', 'Permissions']].map(([id, label]) => <button key={id} role="tab" aria-selected={adminTab === id} className={`tab-button ${adminTab === id ? 'active' : ''}`} onClick={() => setAdminTab(id)}>{label}</button>)}
            </div>

            {adminTab === 'identities' && <div className="admin-content">
              <div className="form-grid">
                <form className="form-card" onSubmit={(event) => submitAdmin(event, async () => { const response = await apiRequest('/admin/teams', { method: 'POST', body: JSON.stringify({ slug: adminForm.teamSlug, name: adminForm.teamName }) }); await readApiJson(response, 'Create team'); setForm('teamSlug', ''); setForm('teamName', '') })}>
                  <h2>Create team</h2><label className="field-label" htmlFor="team-slug">Team slug</label><input id="team-slug" className="text-input" placeholder="team-slug" value={adminForm.teamSlug} onChange={(event) => setForm('teamSlug', event.target.value)} required /><label className="field-label" htmlFor="team-name">Team name</label><input id="team-name" className="text-input" placeholder="Team name" value={adminForm.teamName} onChange={(event) => setForm('teamName', event.target.value)} required /><button className="primary-button">Add team</button>
                </form>
                <form className="form-card" onSubmit={(event) => submitAdmin(event, async () => { const response = await apiRequest('/admin/identities', { method: 'POST', body: JSON.stringify({ team_id: Number(adminForm.teamId), name: adminForm.identityName, role: adminForm.role, identity_type: adminForm.identityType }) }); await readApiJson(response, 'Create identity'); setForm('identityName', '') })}>
                  <h2>Create identity</h2><label className="field-label" htmlFor="identity-team">Team</label><select id="identity-team" className="text-input" value={adminForm.teamId} onChange={(event) => setForm('teamId', event.target.value)} required><option value="">Select team</option>{adminData.teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select><label className="field-label" htmlFor="identity-name">Identity name</label><input id="identity-name" className="text-input" placeholder="Name" value={adminForm.identityName} onChange={(event) => setForm('identityName', event.target.value)} required /><div className="inline-fields"><label className="field-label">Role<select className="text-input" value={adminForm.role} onChange={(event) => setForm('role', event.target.value)}><option>viewer</option><option>operator</option><option>admin</option></select></label><label className="field-label">Type<select className="text-input" value={adminForm.identityType} onChange={(event) => setForm('identityType', event.target.value)}><option value="user">user</option><option value="service">service</option></select></label></div><button className="primary-button">Add identity</button>
                </form>
              </div>
              <section className="surface-card"><div className="section-heading"><div><h2>Teams</h2><p>{adminData.teams.length} registered</p></div></div><div className="table-wrap"><table><thead><tr><th>Team</th><th>Slug</th><th>ID</th></tr></thead><tbody>{adminData.teams.map((team) => <tr key={team.id}><td>{team.name}</td><td><code>{team.slug}</code></td><td>{team.id}</td></tr>)}</tbody></table></div></section>
              <section className="surface-card"><div className="section-heading"><div><h2>Identities</h2><p>Roles determine which administrative actions are available.</p></div></div><div className="table-wrap"><table><thead><tr><th>Identity</th><th>Role</th><th>Type</th><th>Team</th></tr></thead><tbody>{adminData.identities.map((item) => <tr key={item.id}><td>{item.name}</td><td><StatusBadge status={item.role} /></td><td>{item.identity_type}</td><td>{adminData.teams.find((team) => team.id === item.team_id)?.name || item.team_id}</td></tr>)}</tbody></table></div></section>
            </div>}

            {adminTab === 'keys' && <div className="admin-content">
                <form className="form-card key-issue-card" onSubmit={(event) => submitAdmin(event, async () => { const response = await apiRequest('/admin/keys', { method: 'POST', body: JSON.stringify({ identity_id: Number(adminForm.keyIdentity) }) }); const issued = await readApiJson(response, 'Issue API key'); if (typeof issued.api_key !== 'string' || !issued.api_key) throw new Error('API key issuance returned no one-time key.'); setCopyFeedback(''); setOneTimeKey(issued.api_key) })}>
                <div><h2>Issue API key</h2><p>Generated by the gateway and displayed once. Copy it directly into the intended client.</p></div><label className="field-label" htmlFor="key-identity">Identity</label><select id="key-identity" className="text-input" value={adminForm.keyIdentity} onChange={(event) => setForm('keyIdentity', event.target.value)} required><option value="">Select identity</option>{adminData.identities.map((user) => <option key={user.id} value={user.id}>{user.name} · {user.role}</option>)}</select><button className="primary-button">Generate key</button>
                {oneTimeKey && <div className="one-time-key"><strong>Copy this key now; it will not be shown again.</strong><code>{oneTimeKey}</code><button type="button" className="secondary-button" onClick={copyOneTimeKey}>Copy key</button><p className={`copy-feedback${copyFeedback.startsWith('Could not') ? ' error' : ''}`} role="status" aria-live="polite">{copyFeedback}</p></div>}
              </form>
              <section className="surface-card"><div className="section-heading"><div><h2>API keys</h2><p>Only prefixes and revocation state are shown.</p></div></div><div className="table-wrap"><table><thead><tr><th>Identity</th><th>Key prefix</th><th>Status</th><th>Action</th></tr></thead><tbody>{adminData.keys.map((key) => <tr key={key.id}><td>{key.identity_name}</td><td><code>{key.key_prefix}…</code></td><td><StatusBadge status={key.revoked_at ? 'Revoked' : 'Active'} /></td><td>{!key.revoked_at && <button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/keys/${key.id}`, { method: 'DELETE' }); if (!response.ok) throw new Error('Unable to revoke key'); if (oneTimeKey) setOneTimeKey('') })}>Revoke</button>}</td></tr>)}</tbody></table></div></section>
            </div>}

            {adminTab === 'permissions' && <div className="admin-content">
              <form className="form-card permission-form" onSubmit={(event) => submitAdmin(event, async () => { const response = await apiRequest('/admin/permissions', { method: 'POST', body: JSON.stringify({ subject_type: adminForm.subjectType, subject_id: Number(adminForm.subjectId), server_name: adminForm.server, tool_name: adminForm.tool }) }); await readApiJson(response, 'Grant tool access') })}>
                <h2>Grant tool access</h2><div className="inline-fields"><label className="field-label">Subject type<select className="text-input" value={adminForm.subjectType} onChange={(event) => { setForm('subjectType', event.target.value); setForm('subjectId', '') }}><option value="team">Team</option><option value="identity">Identity</option></select></label><label className="field-label">Subject<select className="text-input" value={adminForm.subjectId} onChange={(event) => setForm('subjectId', event.target.value)} required><option value="">Select {adminForm.subjectType}</option>{(adminForm.subjectType === 'team' ? adminData.teams : adminData.identities).map((subject) => <option key={subject.id} value={subject.id}>{subject.name}</option>)}</select></label></div><div className="inline-fields"><label className="field-label">Server<select className="text-input" value={adminForm.server} onChange={(event) => setForm('server', event.target.value)} required><option value="">Select server</option>{serverNames.map((name) => <option value={name} key={name}>{name}</option>)}<option value="*">All registered servers</option></select></label><label className="field-label">Tool name<input className="text-input" value={adminForm.tool} onChange={(event) => setForm('tool', event.target.value)} placeholder="* or tool name" required /></label></div><button className="primary-button">Grant access</button>
              </form>
              <section className="surface-card"><div className="section-heading"><div><h2>Explicit permissions</h2><p>Without an explicit grant, access is denied by default.</p></div></div><div className="table-wrap"><table><thead><tr><th>Subject</th><th>Server</th><th>Tool</th><th>Action</th></tr></thead><tbody>{adminData.permissions.map((permission) => <tr key={`${permission.subject_type}-${permission.subject_id}-${permission.server_name}-${permission.tool_name}`}><td>{permission.subject_type} #{permission.subject_id}</td><td>{permission.server_name}</td><td><code>{permission.tool_name}</code></td><td><button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const query = new URLSearchParams(permission).toString(); const response = await apiRequest(`/admin/permissions?${query}`, { method: 'DELETE' }); if (!response.ok) throw new Error('Unable to remove permission') })}>Remove</button></td></tr>)}</tbody></table></div></section>
            </div>}
          </section>}

          {section === 'audit' && identity.role === 'admin' && <section className="page-section surface-card audit-card">
            <ToolCallMetrics metrics={adminData.metrics} error={metricsError} />
            <div className="section-heading"><div><h2>Recent activity</h2><p>Showing the latest 100 audit records returned by the gateway.</p></div><span className="count-pill">{filteredAudit.length} shown</span></div>
            <div className="filter-bar"><label className="filter-field">Search loaded events<input className="text-input" value={auditFilter.query} onChange={(event) => setAuditFilter((current) => ({ ...current, query: event.target.value }))} placeholder="Identity, server, tool, outcome…" /></label><label className="filter-field">Outcome<select className="text-input" value={auditFilter.outcome} onChange={(event) => setAuditFilter((current) => ({ ...current, outcome: event.target.value }))}><option value="all">All outcomes</option><option value="success">Success</option><option value="failure">Failure</option><option value="denied">Denied</option></select></label></div>
            <p className="muted-note filter-scope">Filters apply only to the 100 records currently loaded; they do not query or filter the full audit history.</p>
            <div className="table-wrap"><table><thead><tr><th>Time</th><th>Identity</th><th>Server / tool</th><th>Outcome</th><th>Duration</th></tr></thead><tbody>{filteredAudit.map((event) => <tr key={event.id}><td>{event.created_at}</td><td>{identityNamesById[event.identity_id] || (event.identity_id ? `Identity ${event.identity_id}` : 'Unauthenticated')}</td><td>{event.server_name || '—'}{event.tool_name ? <><span className="table-separator">/</span>{event.tool_name}</> : ''}</td><td><div className="outcome-cell"><StatusBadge status={event.outcome} />{event.error_class && <small>{event.error_class}</small>}</div></td><td>{event.duration_ms == null ? '—' : `${event.duration_ms} ms`}</td></tr>)}</tbody></table>{!filteredAudit.length && <div className="empty-state">No events match these filters.</div>}</div>
          </section>}

          <footer className="page-footer"><span>MCPHub · Developer platform</span><span>Gateway routes through <code>/api</code>; upstreams remain server-side.</span></footer>
        </div>
      </section>
    </main>
  )
}

export default App
