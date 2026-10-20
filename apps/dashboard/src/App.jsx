import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

const SERVERS = [
  { id: 'woocommerce', name: 'WooCommerce', detail: 'Products and orders' },
  { id: 'inventory', name: 'Inventory', detail: 'Stock and availability' },
  { id: 'support', name: 'Support', detail: 'Customer support tickets' },
]

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

function App() {
  const [apiKey, setApiKey] = useState('')
  const [keyInput, setKeyInput] = useState('')
  const [identity, setIdentity] = useState(null)
  const [section, setSection] = useState('tools')
  const [adminData, setAdminData] = useState({ teams: [], identities: [], keys: [], permissions: [], audit: [], upstreams: [] })
  const [adminForm, setAdminForm] = useState({ teamSlug: '', teamName: '', teamId: '', identityName: '', role: 'viewer', identityType: 'user', keyIdentity: '', subjectType: 'team', subjectId: '', server: 'inventory', tool: '*', newUpstreamName: '', newUpstreamUrl: '', editUpstreamId: '', editUpstreamName: '', editUpstreamUrl: '' })
  const [oneTimeKey, setOneTimeKey] = useState('')
  const [tools, setTools] = useState([])
  const [selectedTool, setSelectedTool] = useState('')
  const [serverFilter, setServerFilter] = useState('all')
  const [argumentsText, setArgumentsText] = useState('{}')
  const [result, setResult] = useState(null)
  const [loadingTools, setLoadingTools] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [gatewayOnline, setGatewayOnline] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState([])
  const [chatBusy, setChatBusy] = useState(false)

  const apiRequest = useCallback((path, options = {}) => fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      'X-API-Key': apiKey,
      ...options.headers,
    },
  }), [apiKey])

  const loadTools = useCallback(async () => {
    if (!apiKey) return
    setLoadingTools(true)
    setError('')
    try {
      const response = await apiRequest('/tools')
      const data = await readApiJson(response, 'Tool discovery')
      if (!Array.isArray(data)) throw new Error('Unexpected tool-list response')
      setTools(data)
      setGatewayOnline(true)
      setSelectedTool((current) =>
        data.some((tool) => tool.name === current) ? current : data[0]?.name || '',
      )
    } catch (err) {
      setGatewayOnline(false)
      setError(`${err.message}. Check the gateway port-forward and retry.`)
    } finally {
      setLoadingTools(false)
    }
  }, [apiKey, apiRequest])

  useEffect(() => {
    if (!apiKey) return undefined
    const refresh = setTimeout(() => loadTools(), 0)
    return () => clearTimeout(refresh)
  }, [apiKey, loadTools])

  useEffect(() => {
    if (!apiKey) return
    apiRequest('/whoami')
      .then(async (response) => {
        setIdentity(await readApiJson(response, 'Identity check'))
      })
      .catch((err) => {
        setIdentity(null)
        if (/HTTP 401\b/.test(err.message)) {
          setApiKey('')
          setError('API key was rejected or revoked. Sign in with a valid key.')
        } else {
          setError(err.message)
        }
      })
  }, [apiKey, apiRequest])

  const loadAdminData = useCallback(async () => {
    const paths = ['/admin/teams', '/admin/identities', '/admin/keys', '/admin/permissions', '/admin/audit?limit=100', '/admin/upstreams']
    const responses = await Promise.all(paths.map((path) => apiRequest(path)))
    const values = await Promise.all(responses.map(async (response) => {
      const data = await readApiJson(response, 'Admin request')
      return data
    }))
    setAdminData({ teams: values[0], identities: values[1], keys: values[2], permissions: values[3], audit: values[4], upstreams: values[5] })
  }, [apiRequest])

  useEffect(() => {
    if (identity?.role === 'admin' && ['admin', 'upstreams'].includes(section)) {
      const refresh = setTimeout(() => {
        loadAdminData().catch((err) => setError(err.message))
      }, 0)
      return () => clearTimeout(refresh)
    }
    return undefined
  }, [identity, section, loadAdminData])

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
    setTools([])
    setOneTimeKey('')
    setSection('tools')
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

  function setForm(field, value) {
    setAdminForm((current) => ({ ...current, [field]: value }))
  }

  function newApiKey() {
    const bytes = crypto.getRandomValues(new Uint8Array(48))
    return `mcp_${btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')}`
  }

  const visibleTools = useMemo(
    () => tools.filter((tool) => serverFilter === 'all' || tool.server === serverFilter),
    [tools, serverFilter],
  )

  const activeTool = tools.find((tool) => tool.name === selectedTool)

  function chooseTool(name) {
    setSelectedTool(name)
    setArgumentsText(JSON.stringify(EXAMPLES[name] ?? {}, null, 2))
    setResult(null)
    setError('')
  }

  async function runTool(event) {
    event.preventDefault()
    setError('')
    setResult(null)

    let args
    try {
      args = JSON.parse(argumentsText)
      if (!args || Array.isArray(args) || typeof args !== 'object') {
        throw new Error('Arguments must be a JSON object.')
      }
    } catch (err) {
      setError(`Invalid JSON: ${err.message}`)
      return
    }

    setRunning(true)
    try {
      const response = await apiRequest('/call', {
        method: 'POST',
        body: JSON.stringify({ tool: selectedTool, arguments: args }),
      })
      const data = await readApiJson(response, 'Tool call', { allowErrorStatus: true })
      setResult(data)
      if (!response.ok) setError(data.detail || data.error || `HTTP ${response.status}`)
      else if (data.is_error) setError('The MCP tool returned an error.')
    } catch (err) {
      setError(`Request failed: ${err.message}`)
    } finally {
      setRunning(false)
    }
  }

  async function sendChat(event) {
    event.preventDefault()
    const prompt = chatInput.trim()
    if (!prompt || chatBusy) return
    setChatInput('')
    setChatMessages((messages) => [...messages, { role: 'user', text: prompt }])
    setChatBusy(true)
    try {
      const messages = [
        ...chatMessages.map((message) => ({
          role: message.role,
          content: message.text,
        })),
        { role: 'user', content: prompt },
      ].slice(-20)
      const response = await apiRequest('/assistant', {
        method: 'POST',
        body: JSON.stringify({ messages }),
      })
      const data = await readApiJson(response, 'Assistant request')
      setChatMessages((current) => [...current, {
        role: 'assistant',
        text: data.answer || 'The assistant returned no answer.',
        toolCalls: Array.isArray(data.tool_calls) ? data.tool_calls : [],
      }])
    } catch (err) {
      setChatMessages((messages) => [...messages, { role: 'assistant', text: `Could not complete request: ${err.message}` }])
    } finally {
      setChatBusy(false)
    }
  }

  const serverCounts = Object.fromEntries(
    SERVERS.map((server) => [server.id, tools.filter((tool) => tool.server === server.id).length]),
  )
  const identityNamesById = Object.fromEntries(
    adminData.identities.map((record) => [record.id, record.name]),
  )

  if (!apiKey || !identity) return (
    <main className="app-shell login-shell">
      <form className="login-card" onSubmit={signIn}>
        <div className="brand-mark">M</div>
        <div className="eyebrow">MCPHUB ACCESS</div>
        <h1>Sign in to your workspace</h1>
        <p>Use an MCPHub API key. It stays in this tab’s memory and is cleared when you sign out or close the page.</p>
        {error && <div className="alert error-alert">{error}</div>}
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
          <div className="brand-mark">M</div>
          <div>
            <strong>MCPHub</strong>
            <span>Operations console</span>
          </div>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <button className={`nav-item ${section === 'tools' ? 'active' : ''}`} onClick={() => setSection('tools')}><span>?</span> MCP tools <span className="nav-count">{tools.length}</span></button>
        <button className={`nav-item ${section === 'assistant' ? 'active' : ''}`} onClick={() => setSection('assistant')}><span>?</span> Woo assistant</button>
        {identity.role === 'admin' && <button className={`nav-item ${section === 'admin' ? 'active' : ''}`} onClick={() => setSection('admin')}><span>?</span> Administration</button>}
        {identity.role === 'admin' && <button className={`nav-item ${section === 'upstreams' ? 'active' : ''}`} onClick={() => setSection('upstreams')}><span>?</span> Upstream servers</button>}

        <div className="sidebar-bottom">
          <div className={`status-dot ${gatewayOnline ? 'online' : ''}`} />
          <div>
            <strong>{gatewayOnline ? 'Gateway connected' : 'Gateway offline'}</strong>
            <span>Local development</span>
          </div>
        </div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Overview</div>
          <div className="header-actions">
            {identity && <span className="identity-label">{identity.name} · {identity.role}</span>}
            {section === 'tools' && <button className="secondary-button" onClick={loadTools} disabled={loadingTools}>{loadingTools ? 'Refreshing…' : '? Refresh tools'}</button>}
            <button className="secondary-button" onClick={signOut}>Sign out</button>
          </div>
        </header>

        <div className="page-content">
          <section className="page-heading">
            <div>
              <div className="eyebrow">MODEL CONTEXT PROTOCOL</div>
              <h1>Operations overview</h1>
              <p>Discover and execute tools across your connected MCP servers.</p>
            </div>
            <div className={`connection-pill ${gatewayOnline ? 'connected' : 'disconnected'}`}>
              <span /> {gatewayOnline ? 'Gateway online' : 'Gateway offline'}
            </div>
          </section>

          {error && (!gatewayOnline || section === 'admin') && (
            <div className="alert error-alert">
              <strong>Connection issue</strong>
              <span>{error}</span>
            </div>
          )}

          <section className="stats-grid">
            <article className="stat-card">
              <div className="stat-top"><span>Registered tools</span><span className="stat-icon purple">?</span></div>
              <div className="stat-number">{loadingTools ? '—' : tools.length}</div>
              <div className="stat-caption">Discovered from upstream servers</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Connected servers</span><span className="stat-icon green">?</span></div>
              <div className="stat-number">{loadingTools ? '—' : new Set(tools.map((tool) => tool.server)).size}<span className="stat-total"> / 3</span></div>
              <div className="stat-caption">WooCommerce, Inventory, Support</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Gateway status</span><span className="stat-icon blue">?</span></div>
              <div className={`stat-status ${gatewayOnline ? 'text-green' : 'text-red'}`}>
                <span className="status-dot" /> {gatewayOnline ? 'Operational' : 'Unavailable'}
              </div>
              <div className="stat-caption">API endpoint via local proxy</div>
            </article>
          </section>

          <section className="servers-section">
            <div className="section-heading">
              <div><h2>Upstream servers</h2><p>Tools discovered from each connected service</p></div>
            </div>
            <div className="server-grid">
              {SERVERS.map((server) => (
                <article className="server-card" key={server.id}>
                  <div className={`server-icon ${server.id}`}>
                    {server.id === 'woocommerce' ? 'W' : server.id === 'inventory' ? 'I' : 'S'}
                  </div>
                  <div className="server-info">
                    <strong>{server.name}</strong>
                    <span>{server.detail}</span>
                  </div>
                  <div className="server-tools">{serverCounts[server.id] || 0} tools</div>
                  <div className={`server-indicator ${(serverCounts[server.id] || 0) > 0 ? 'ready' : ''}`} />
                </article>
              ))}
            </div>
          </section>

          {section === 'tools' && <section className="tool-section">
            <div className="section-heading">
              <div><h2>Tool explorer</h2><p>Select a tool, provide arguments, and inspect its response.</p></div>
              <span className="tool-total">{tools.length} tools</span>
            </div>

            <div className="explorer-layout">
              <div className="tool-list-panel">
                <div className="tool-list-header">
                  <strong>Available tools</strong>
                  <select value={serverFilter} onChange={(event) => setServerFilter(event.target.value)} aria-label="Filter by server">
                    <option value="all">All servers</option>
                    {SERVERS.map((server) => <option value={server.id} key={server.id}>{server.name}</option>)}
                  </select>
                </div>
                <div className="tool-list">
                  {loadingTools && <div className="empty-state">Loading tools…</div>}
                  {!loadingTools && visibleTools.map((tool) => (
                    <button
                      key={`${tool.server}-${tool.name}`}
                      className={`tool-item ${selectedTool === tool.name ? 'selected' : ''}`}
                      onClick={() => chooseTool(tool.name)}
                    >
                      <span className={`tool-symbol ${tool.server}`}>?</span>
                      <span className="tool-item-text"><strong>{tool.name}</strong><small>{tool.server}</small></span>
                      <span className="tool-chevron">›</span>
                    </button>
                  ))}
                  {!loadingTools && visibleTools.length === 0 && <div className="empty-state">No tools found. Refresh the list.</div>}
                </div>
              </div>

              <div className="tool-workspace">
                {activeTool ? (
                  <>
                    <div className="active-tool-heading">
                      <div>
                        <div className="tool-kicker">SELECTED TOOL</div>
                        <h3>{activeTool.name}</h3>
                        <p>{activeTool.description || 'No description provided.'}</p>
                      </div>
                      <span className={`server-tag ${activeTool.server}`}>{activeTool.server}</span>
                    </div>

                    <form onSubmit={runTool}>
                      <label className="field-label" htmlFor="tool-arguments">Arguments <span>JSON</span></label>
                      <textarea
                        id="tool-arguments"
                        className="json-editor"
                        value={argumentsText}
                        onChange={(event) => setArgumentsText(event.target.value)}
                        spellCheck="false"
                        rows={10}
                        aria-label="Tool arguments JSON"
                      />
                      <div className="form-footer">
                        <span>Send arguments to the selected MCP tool.</span>
                        <button className="primary-button" type="submit" disabled={running || !gatewayOnline || !selectedTool}>
                          {running ? <><span className="spinner" /> Running…</> : '? Run tool'}
                        </button>
                      </div>
                    </form>

                    {(result || error) && (
                      <div className="result-panel">
                        <div className="result-header">
                          <strong>Execution result</strong>
                          {result && <span className={`result-badge ${result.is_error || error ? 'failed' : 'success'}`}>{result.is_error || error ? 'Error' : 'Success'}</span>}
                        </div>
                        {error && <div className="result-error">{error}</div>}
                        {result && <pre>{JSON.stringify(result, null, 2)}</pre>}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="empty-workspace">
                    <div className="empty-symbol">?</div>
                    <h3>{loadingTools ? 'Discovering tools' : 'Select a tool'}</h3>
                    <p>{loadingTools ? 'Connecting to your MCP gateway.' : 'Choose a tool from the list to configure and execute it.'}</p>
                    {!loadingTools && !gatewayOnline && <button className="secondary-button" onClick={loadTools}>Retry connection</button>}
                  </div>
                )}
              </div>
            </div>
          </section>}

          {section === 'assistant' && <section className="tool-section assistant-section">
            <div className="section-heading"><div><h2>MCPHub assistant</h2><p>Ask about operations across the tools available to your account.</p></div></div>
            <div className="assistant-card">
              <div className="assistant-messages" aria-live="polite">
                {chatMessages.length === 0 && <p className="assistant-hint">Ask “What can I ask you?” to see examples based on your available tools.</p>}
                {chatMessages.map((message, index) => <div className={`assistant-message ${message.role}`} key={`${index}-${message.role}`}><strong>{message.role === 'user' ? 'You' : 'MCPHub assistant'}</strong><span>{message.text}</span>{message.toolCalls?.length > 0 && <ul className="assistant-tool-calls">{message.toolCalls.map((call, callIndex) => <li key={`${call.name}-${callIndex}`}><code>{call.name}</code><span className={call.outcome === 'succeeded' ? 'tool-outcome success' : 'tool-outcome failed'}>{call.outcome}</span></li>)}</ul>}</div>)}
                {chatBusy && <div className="assistant-hint">Calling the gateway…</div>}
              </div>
              <form className="assistant-form" onSubmit={sendChat}>
                <input className="text-input" aria-label="Message WooCommerce assistant" value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="Ask about products, orders, inventory, or support" disabled={chatBusy || !gatewayOnline} />
                <button className="primary-button" type="submit" disabled={chatBusy || !gatewayOnline || !chatInput.trim()}>{chatBusy ? 'Working…' : 'Send'}</button>
              </form>
            </div>
          </section>}

          {section === 'upstreams' && identity.role === 'admin' && <section className="tool-section admin-section">
            <div className="section-heading"><div><h2>MCP upstreams</h2><p>Manage gateway registrations. Endpoint URLs are kept server-side and are never returned by the API.</p></div><button className="secondary-button" onClick={() => loadAdminData().catch((err) => setError(err.message))}>Refresh</button></div>
            <div className="admin-grid">
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const response = await apiRequest('/admin/upstreams', { method: 'POST', body: JSON.stringify({ name: adminForm.newUpstreamName, endpoint_url: adminForm.newUpstreamUrl, enabled: true }) })
                await readApiJson(response, 'Add upstream')
                setForm('newUpstreamName', ''); setForm('newUpstreamUrl', '')
                await loadTools()
              })}>
                <h3>Add upstream</h3><input className="text-input" placeholder="server-name" value={adminForm.newUpstreamName} onChange={(event) => setForm('newUpstreamName', event.target.value)} required /><input className="text-input" type="url" placeholder="https://service.example/mcp" value={adminForm.newUpstreamUrl} onChange={(event) => setForm('newUpstreamUrl', event.target.value)} required /><button className="primary-button">Add upstream</button>
              </form>
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const response = await apiRequest(`/admin/upstreams/${adminForm.editUpstreamId}`, { method: 'PUT', body: JSON.stringify({ name: adminForm.editUpstreamName, endpoint_url: adminForm.editUpstreamUrl }) })
                await readApiJson(response, 'Edit upstream')
                setForm('editUpstreamId', ''); setForm('editUpstreamName', ''); setForm('editUpstreamUrl', '')
                await loadTools()
              })}>
                <h3>Edit upstream</h3><select className="text-input" value={adminForm.editUpstreamId} onChange={(event) => { const selected = adminData.upstreams.find((item) => String(item.id) === event.target.value); setForm('editUpstreamId', event.target.value); setForm('editUpstreamName', selected?.name || '') }} required><option value="">Select upstream</option>{adminData.upstreams.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><input className="text-input" placeholder="New server name" value={adminForm.editUpstreamName} onChange={(event) => setForm('editUpstreamName', event.target.value)} required /><input className="text-input" type="url" placeholder="New Streamable HTTP URL" value={adminForm.editUpstreamUrl} onChange={(event) => setForm('editUpstreamUrl', event.target.value)} required /><button className="primary-button">Save upstream</button>
              </form>
            </div>
            <div className="admin-card wide-card"><h3>Registered upstreams</h3><div className="admin-table">{adminData.upstreams.map((item) => <div className="admin-row" key={item.id}><span>{item.name}</span><span>{item.enabled ? 'enabled' : 'disabled'}</span><button className="secondary-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/upstreams/${item.id}/enabled`, { method: 'PATCH', body: JSON.stringify({ enabled: !item.enabled }) }); await readApiJson(response, 'Change upstream status'); await loadTools() })}>{item.enabled ? 'Disable' : 'Enable'}</button><button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/upstreams/${item.id}`, { method: 'DELETE' }); await readApiJson(response, 'Remove upstream'); await loadTools() })}>Remove</button></div>)}{adminData.upstreams.length === 0 && <p>No upstreams are registered.</p>}</div></div>
          </section>}

          {section === 'admin' && identity.role === 'admin' && <section className="tool-section admin-section">
            <div className="section-heading"><div><h2>Administration</h2><p>Manage teams, service identities, keys, permissions, and the audit trail.</p></div><button className="secondary-button" onClick={() => loadAdminData().catch((err) => setError(err.message))}>Refresh</button></div>
            <div className="admin-grid">
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const response = await apiRequest('/admin/teams', { method: 'POST', body: JSON.stringify({ slug: adminForm.teamSlug, name: adminForm.teamName }) })
                await readApiJson(response, 'Create team')
                setForm('teamSlug', ''); setForm('teamName', '')
              })}>
                <h3>Create team</h3><input className="text-input" placeholder="team-slug" value={adminForm.teamSlug} onChange={(event) => setForm('teamSlug', event.target.value)} required /><input className="text-input" placeholder="Team name" value={adminForm.teamName} onChange={(event) => setForm('teamName', event.target.value)} required /><button className="primary-button">Add team</button>
              </form>
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const response = await apiRequest('/admin/identities', { method: 'POST', body: JSON.stringify({ team_id: Number(adminForm.teamId), name: adminForm.identityName, role: adminForm.role, identity_type: adminForm.identityType }) })
                await readApiJson(response, 'Create identity')
                setForm('identityName', '')
              })}>
                <h3>Create identity</h3><select className="text-input" value={adminForm.teamId} onChange={(event) => setForm('teamId', event.target.value)} required><option value="">Select team</option>{adminData.teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select><input className="text-input" placeholder="Identity name" value={adminForm.identityName} onChange={(event) => setForm('identityName', event.target.value)} required /><div className="inline-fields"><select className="text-input" value={adminForm.role} onChange={(event) => setForm('role', event.target.value)}><option>viewer</option><option>operator</option><option>admin</option></select><select className="text-input" value={adminForm.identityType} onChange={(event) => setForm('identityType', event.target.value)}><option value="user">user</option><option value="service">service</option></select></div><button className="primary-button">Add identity</button>
              </form>
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const apiKeyValue = newApiKey()
                const response = await apiRequest('/admin/keys', { method: 'POST', body: JSON.stringify({ identity_id: Number(adminForm.keyIdentity), api_key: apiKeyValue }) })
                await readApiJson(response, 'Issue API key')
                setOneTimeKey(apiKeyValue)
              })}>
                <h3>Issue API key</h3><select className="text-input" value={adminForm.keyIdentity} onChange={(event) => setForm('keyIdentity', event.target.value)} required><option value="">Select identity</option>{adminData.identities.map((user) => <option key={user.id} value={user.id}>{user.name} · {user.role}</option>)}</select><button className="primary-button">Generate key</button>{oneTimeKey && <div className="one-time-key"><strong>Copy this key now. It will not be shown again.</strong><code>{oneTimeKey}</code><button type="button" className="secondary-button" onClick={() => navigator.clipboard.writeText(oneTimeKey)}>Copy key</button></div>}
              </form>
              <form className="admin-card" onSubmit={(event) => submitAdmin(event, async () => {
                const response = await apiRequest('/admin/permissions', { method: 'POST', body: JSON.stringify({ subject_type: adminForm.subjectType, subject_id: Number(adminForm.subjectId), server_name: adminForm.server, tool_name: adminForm.tool }) })
                await readApiJson(response, 'Grant tool access')
              })}>
                <h3>Grant tool access</h3><select className="text-input" value={adminForm.subjectType} onChange={(event) => setForm('subjectType', event.target.value)}><option value="team">team</option><option value="identity">identity</option></select><select className="text-input" value={adminForm.subjectId} onChange={(event) => setForm('subjectId', event.target.value)} required><option value="">Select {adminForm.subjectType}</option>{(adminForm.subjectType === 'team' ? adminData.teams : adminData.identities).map((subject) => <option key={subject.id} value={subject.id}>{subject.name}</option>)}</select><div className="inline-fields"><select className="text-input" value={adminForm.server} onChange={(event) => setForm('server', event.target.value)}><option value="woocommerce">woocommerce</option><option value="inventory">inventory</option><option value="support">support</option><option value="*">all servers</option></select><input className="text-input" value={adminForm.tool} onChange={(event) => setForm('tool', event.target.value)} placeholder="* or tool name" /></div><button className="primary-button">Grant access</button>
              </form>
            </div>
            {oneTimeKey && <div className="alert info-alert">New API key is displayed above once. Copy it into the intended client now.</div>}
            <div className="admin-card wide-card"><h3>Active and revoked keys</h3><div className="admin-table">{adminData.keys.map((key) => <div className="admin-row" key={key.id}><span>{key.identity_name} · {key.key_prefix}…</span><span>{key.revoked_at ? 'revoked' : 'active'}</span>{!key.revoked_at && <button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const response = await apiRequest(`/admin/keys/${key.id}`, { method: 'DELETE' }); if (!response.ok) throw new Error('Unable to revoke key') })}>Revoke</button>}</div>)}{adminData.keys.length === 0 && <p>No API keys yet.</p>}</div></div>
            <div className="admin-card wide-card"><h3>Permissions</h3><div className="admin-table">{adminData.permissions.map((permission) => <div className="admin-row" key={`${permission.subject_type}-${permission.subject_id}-${permission.server_name}-${permission.tool_name}`}><span>{permission.subject_type} #{permission.subject_id}</span><span>{permission.server_name} / {permission.tool_name}</span><button className="danger-button" onClick={() => submitAdmin({ preventDefault() {} }, async () => { const query = new URLSearchParams(permission).toString(); const response = await apiRequest(`/admin/permissions?${query}`, { method: 'DELETE' }); if (!response.ok) throw new Error('Unable to remove permission') })}>Remove</button></div>)}{adminData.permissions.length === 0 && <p>No explicit grants. Access is denied by default.</p>}</div></div>
            <div className="admin-card wide-card"><h3>Recent audit events</h3><div className="admin-table">{adminData.audit.map((event) => <div className="admin-row" key={event.id}><span>{event.created_at} · {event.event_type}</span><span>{event.identity_id ? identityNamesById[event.identity_id] || `identity ${event.identity_id}` : 'unauthenticated'} · {event.server_name || '—'} / {event.tool_name || '—'}</span><span>{event.duration_ms == null ? '—' : `${event.duration_ms} ms`}</span><span className={`result-badge ${event.outcome === 'success' ? 'success' : 'failed'}`}>{event.outcome}{event.error_class ? ` · ${event.error_class}` : ''}</span></div>)}{adminData.audit.length === 0 && <p>No audit events recorded yet.</p>}</div></div>
          </section>}

          <footer className="page-footer">
            <span>MCPHub · Local development console</span>
            <span>Gateway API: <code>/tools</code> · <code>/call</code></span>
          </footer>
        </div>
      </section>
    </main>
  )
}

export default App
