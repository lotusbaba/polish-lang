// Only semantic content participates in synchronization. Moving, styling, and
// annotating shapes does not change an architecture's meaning.
export function canvasModel(elements, graph) {
  const active = elements.filter(e => !e.isDeleted);
  const byId = new Map(active.map(e => [e.id, e]));
  const label = shape => {
    const text = active.find(e => e.type === 'text' && e.containerId === shape.id);
    return (text?.originalText ?? text?.text ?? '').trim();
  };
  const nodes = [], edges = [];
  graph.nodes.filter(n => n.parent).forEach((node, i) => {
    if (!byId.has(`polish:${node.name}`) || !byId.has(`polish:${node.parent}`)) return;
    const arrow = byId.get(`polish-edge:${graph.edges.length + i}`);
    if (!arrow || arrow.startBinding?.elementId !== `polish:${node.parent}` || arrow.endBinding?.elementId !== `polish:${node.name}` || !['', 'contains'].includes(label(arrow))) {
      throw new Error('Edit containment in the source pane. Use Check & draw to restore the containment arrows.');
    }
  });
  for (const original of graph.nodes) {
    const shape = byId.get(`polish:${original.name}`);
    if (!shape) continue;
    const lines = label(shape).split('\n');
    const name = lines[0]?.trim(), type = lines.slice(1).join(' ').trim();
    const [kind, ...product] = type.split('·').map(s => s.trim());
    const originalProduct = String(original.properties.product || original.properties.engine || '');
    if (product.join('·') !== originalProduct) throw new Error('Edit product properties in the source pane. Canvas labels contain the name and component kind.');
    nodes.push({ id: shape.id, original: original.name, name, kind });
  }
  for (const shape of active) {
    if (!['rectangle', 'ellipse', 'diamond'].includes(shape.type) || shape.customData?.polish) continue;
    const text = label(shape);
    const match = text.match(/^([A-Za-z_]\w*)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\s*\{([\s\S]*)\})?$/);
    if (match && graph.kinds.includes(match[1])) nodes.push({ id: shape.id, name: match[2], kind: match[1], body: match[3] || '' });
  }
  const ids = new Set(nodes.map(n => n.id));
  for (const shape of active) {
    if (shape.type !== 'arrow' || shape.customData?.relation === 'contains') continue;
    const original = shape.customData?.polish ? Number(shape.id.replace('polish-edge:', '')) : null;
    const source = shape.startBinding?.elementId, target = shape.endBinding?.elementId;
    if (original === null && (!ids.has(source) || !ids.has(target))) continue; // Freehand annotation.
    if (original !== null) {
      const old = graph.edges[original];
      if (!old) throw new Error('This scene belongs to a different architecture. Use Check & draw.');
      if (!byId.has(`polish:${old.source}`) || !byId.has(`polish:${old.target}`)) continue;
    }
    const [kind, ...pattern] = label(shape).split(/\s+/);
    if (!kind) throw new Error('Label the connected arrow with a Polish relationship, for example invokes or hosted_on.');
    edges.push({ id: shape.id, original, source, target, kind, pattern: kind === 'routes_to' && pattern.length ? pattern.join(' ') : null });
  }
  return { nodes, edges };
}
