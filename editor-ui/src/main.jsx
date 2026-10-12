import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Excalidraw, MainMenu, CaptureUpdateAction } from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';
import { architectureElements } from './diagram';
import { canvasModel } from './canvas';
import { loadDocument, storeDocument } from './storage';
import './style.css';

const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
async function request(path, payload) {
  const response = await fetch(`/api/${path}`, { method: payload ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}`, ...(payload ? { 'Content-Type': 'application/json' } : {}) }, body: payload ? JSON.stringify(payload) : undefined });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request failed');
  return result;
}
function download(text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function App() {
  const [session, setSession] = useState(null), [source, setSource] = useState(''), [api, setApi] = useState(null);
  const [initial, setInitial] = useState(null), [report, setReport] = useState(null), [graph, setGraph] = useState(null);
  const [message, setMessage] = useState('Opening your workspace…'), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [showSource, setShowSource] = useState(true), [selected, setSelected] = useState(null);
  const baseline = useRef(null), syncTimer = useRef(null), syncSequence = useRef(0);
  const [canvasProblem, setCanvasProblem] = useState(''), [syncing, setSyncing] = useState(false);
  const state = useRef({}), timer = useRef(null), ready = useRef(false), input = useRef(null);
  state.current = { ...state.current, source, session };
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const current = await request('session');
        let saved;
        try { saved = await loadDocument(current.documentId); } catch { /* Canvas still works without storage. */ }
        if (!active) return;
        const matching = saved && saved.revision === current.revision;
        setSession(current); setSource(matching ? saved.source : current.source);
        setInitial(matching ? saved.scene : {});
        state.current.library = matching ? saved.library : [];
        setMessage(matching && saved.source !== current.source ? 'Restored your local draft' : current.canSave ? 'Connected to ' + current.filename : 'New local workspace');
      } catch (e) { if (active) setError(e.message); }
    })();
    return () => { active = false; clearTimeout(timer.current); clearTimeout(syncTimer.current); };
  }, []);
  function persist() {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const current = state.current;
      if (!current.session || !current.scene) return;
      storeDocument(current.session.documentId, { source: current.source, revision: current.session.revision, scene: current.scene, library: current.library || [] }).catch(() => setError('Browser storage is full or unavailable. Export your diagram to keep it.'));
    }, 400);
  }
  useEffect(() => { if (session) persist(); }, [source, session]);
  function changeSource(text, manual = false) {
    if (manual) { syncSequence.current++; clearTimeout(syncTimer.current); setSyncing(false); }
    state.current.source = text;
    setSource(text);
  }
  function drawGraph(nextGraph, text, arrange = false) {
    clearTimeout(syncTimer.current); syncSequence.current++;
    let previous = api.getSceneElementsIncludingDeleted();
    // Adopt newly drawn architecture shapes when regenerating the diagram so
    // they do not survive as duplicate annotations.
    const model = baseline.current?.model;
    if (model) {
      const ids = new Map(model.nodes.map(n => [n.id, n.name]));
      previous = previous.map(e => ids.has(e.id) ? { ...e, id: `polish:${ids.get(e.id)}`, customData: { polish: true } } :
        ids.has(e.containerId) ? { ...e, customData: { polish: true } } : e);
      const semanticArrows = new Set(model.edges.map(e => e.id).filter(Boolean));
      previous = previous.map(e => semanticArrows.has(e.id) || semanticArrows.has(e.containerId) ? { ...e, customData: { polish: true } } : e);
    }
    const elements = architectureElements(nextGraph, previous, arrange);
    const modelNow = canvasModel(elements, nextGraph);
    baseline.current = { source: text, graph: nextGraph, acceptedSource: text, signature: JSON.stringify(modelNow), model: modelNow };
    setCanvasProblem(''); setSyncing(false);
    api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  }
  async function syncCanvas(elements = api?.getSceneElementsIncludingDeleted()) {
    clearTimeout(syncTimer.current);
    const base = baseline.current;
    if (!base || !elements) return { ok: true, source: state.current.source };
    let model;
    try { model = canvasModel(elements, base.graph); }
    catch (e) { setCanvasProblem(e.message); return { ok: false }; }
    const signature = JSON.stringify(model);
    if (signature === base.signature) { setCanvasProblem(''); return { ok: true, source: state.current.source }; }
    if (state.current.source !== base.acceptedSource) {
      setCanvasProblem('The source also changed. Use Check & draw to apply the source before editing the canvas again.');
      return { ok: false };
    }
    const sequence = ++syncSequence.current;
    setSyncing(true); setCanvasProblem('');
    try {
      const result = await request('canvas', { source: base.source, ...model });
      if (sequence !== syncSequence.current || baseline.current !== base || state.current.source !== base.acceptedSource) return { ok: false };
      setReport(result);
      if (!result.ok) {
        setCanvasProblem('Canvas change is invalid. Fix the diagram or use Check & draw to restore the source diagram.');
        return { ok: false };
      }
      base.acceptedSource = result.source; base.signature = signature; base.model = model;
      changeSource(result.source); setGraph(result.graph);
      setMessage('Canvas changes applied to the specification · Save file to write to disk');
      return { ok: true, source: result.source };
    } catch (e) { if (sequence === syncSequence.current) setCanvasProblem(e.message); return { ok: false }; }
    finally { if (sequence === syncSequence.current) setSyncing(false); }
  }
  function queueCanvasSync(elements, appState) {
    clearTimeout(syncTimer.current);
    if (!baseline.current || appState.editingTextElement || appState.editingElement || appState.draggingElement || appState.resizingElement) return;
    syncTimer.current = setTimeout(() => syncCanvas(elements), 600);
  }
  async function evaluate(simulate = false, draw = true, arrange = false) {
    setBusy(true); setError('');
    try {
      const useCanvas = simulate || (!canvasProblem && state.current.source === baseline.current?.acceptedSource);
      const synced = useCanvas ? await syncCanvas() : { ok: true, source: state.current.source };
      if (!synced.ok) return;
      const result = await request(simulate ? 'simulate' : 'check', { source: synced.source });
      if (state.current.source !== synced.source) return;
      setReport(result);
      if (result.graph) {
        setGraph(result.graph);
        if (draw && api) drawGraph(result.graph, synced.source, arrange);
        if (arrange) api.scrollToContent(undefined, { fitToViewport: true, viewportZoomFactor: 0.8 });
        setMessage(simulate ? `${result.results.filter(r => r.passed).length}/${result.results.length} scenarios passed` : `${result.graph.nodes.length} components · ${result.graph.edges.length} relationships`);
      } else setMessage('Fix the specification errors to update the diagram');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  useEffect(() => {
    if (api && session && !ready.current) {
      ready.current = true;
      request('check', { source }).then(result => {
        setReport(result); setGraph(result.graph);
        if (result.graph) {
          drawGraph(result.graph, source);
          api.scrollToContent(undefined, { fitToViewport: true, viewportZoomFactor: 0.8 });
          setMessage(`${result.graph.nodes.length} components · ${result.graph.edges.length} relationships`);
        }
      }).catch(e => setError(e.message));
    }
  }, [api, session]);
  async function save() {
    setBusy(true); setError('');
    try {
      const synced = await syncCanvas();
      if (!synced.ok) return;
      const result = await request('save', { source: synced.source, revision: session.revision });
      setSession({ ...session, source: synced.source, revision: result.revision }); setMessage('Saved ' + session.filename);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const node = graph?.nodes.find(n => n.name === selected);
  return <div className="studio">
    <header><div className="brand"><span className="mark">P<span>✦</span></span><div><strong>Polish<span className="studio-name"> Studio</span></strong><small>Architecture, made visible.</small></div></div>
      <div className="document"><span className="local-dot"/> {session?.filename || 'Local workspace'}{session && source !== session.source && <span className="draft">Draft</span>}</div>
      <nav><button onClick={() => setShowSource(v => !v)}>{showSource ? 'Hide source' : 'Show source'}</button><button disabled={!api} onClick={() => api.scrollToContent(undefined, { fitToViewport: true, viewportZoomFactor: 0.8 })}>Fit canvas</button><button disabled={!graph || !api} onClick={() => evaluate(false, true, true)}>Auto-arrange</button></nav>
    </header>
    <main>
      {showSource && <aside className="spec-panel"><div className="panel-title"><div><span className="eyebrow">SOURCE OF TRUTH</span><h2>Specification</h2></div><button title="Import a .polishd file into this draft" onClick={() => input.current.click()}>Open…</button></div>
        <input hidden ref={input} type="file" accept=".polishd,text/plain" onChange={async e => { const file = e.target.files[0]; if (file) { changeSource(await file.text(), true); setMessage('Imported draft; Check & draw to update the canvas'); } e.target.value = ''; }}/>
        <textarea aria-label="Polish specification" spellCheck="false" value={source} onChange={e => changeSource(e.target.value, true)} placeholder="architecture MySystem { … }"/>
        <div className="actions"><button className="primary" disabled={busy || !api} onClick={() => evaluate()}>Check & draw</button><button disabled={busy || !session} onClick={() => evaluate(true)}>Simulate</button><button disabled={busy || !session?.canSave} onClick={save}>Save file</button><button disabled={!session} onClick={async () => { const synced = await syncCanvas(); if (synced.ok) download(synced.source, session.filename); }}>Download .polishd</button></div>
        <p className="hint">Canvas edits update the source draft. Rename a component in its label, delete a component or arrow, or reconnect a labeled arrow. Add a shape labeled <code>{'service Search { hosted_on Apps }'}</code> to create a component. Save file writes the draft to disk.</p>
        <section className="results" aria-label="Validation results"><div className="results-title">{report ? report.ok ? '✓ Checks passed' : 'Review required' : 'Validation & scenarios'}</div>
          {report?.diagnostics.map((d, i) => <div className="diagnostic" key={i}><strong>{d.code} {d.line ? `· line ${d.line}` : ''}</strong><p>{d.message}</p></div>)}
          {report?.results.map(r => <details key={r.scenario}><summary className={r.passed ? 'pass' : 'fail'}>{r.passed ? 'PASS' : 'FAIL'} · {r.scenario}</summary><p>{r.error ? `Observed ${r.error}${r.passed ? ' (expected)' : ''}` : r.outcome}</p><pre>{[...r.trace, ...r.failures].join('\n')}</pre></details>)}
          {!report && <p>Compile your architecture and test its request paths here.</p>}
        </section>
        {node && <section className="inspector"><div className="eyebrow">SELECTED COMPONENT</div><h3>{node.name}</h3><span>{node.kind} · line {node.line}</span><pre>{JSON.stringify({ properties: node.properties, fields: node.fields, requirements: node.requirements }, null, 2)}</pre></section>}
      </aside>}
      <section className="canvas-panel" aria-label="Architecture diagram">
        {session && initial && <Excalidraw excalidrawAPI={setApi} initialData={{ ...initial, libraryItems: state.current.library || [], appState: { viewBackgroundColor: '#fbfcfa', ...initial.appState } }} name={session.filename.replace(/\.polishd$/, '')}
          onChange={(elements, appState, files) => {
            state.current.scene = { elements, files, appState: { viewBackgroundColor: appState.viewBackgroundColor, theme: appState.theme, zoom: appState.zoom, scrollX: appState.scrollX, scrollY: appState.scrollY } };
            const selectedElement = elements.find(e => appState.selectedElementIds[e.id]);
            const id = selectedElement?.customData?.node || selectedElement?.containerId?.replace(/^polish:/, '');
            setSelected(id || null); persist(); queueCanvasSync(elements, appState);
          }} onLibraryChange={items => { state.current.library = items; persist(); }}>
          <MainMenu><MainMenu.DefaultItems.LoadScene/><MainMenu.DefaultItems.SaveToActiveFile/><MainMenu.DefaultItems.Export/><MainMenu.DefaultItems.SaveAsImage/><MainMenu.DefaultItems.ClearCanvas/><MainMenu.Separator/><MainMenu.DefaultItems.ToggleTheme/><MainMenu.DefaultItems.ChangeCanvasBackground/><MainMenu.DefaultItems.Help/></MainMenu>
        </Excalidraw>}
      </section>
    </main>
    <footer role="status"><span className={error || canvasProblem ? 'error' : ''}>{error || canvasProblem || (syncing ? 'Updating specification from canvas…' : message)}</span><span>Local workspace · Excalidraw canvas</span></footer>
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
