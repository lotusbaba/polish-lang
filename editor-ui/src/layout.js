import dagre from '@dagrejs/dagre';

export const NODE_WIDTH = 240, NODE_HEIGHT = 112;

// Keep every semantic node visible/editable. Compact layouts wrap families
// (a component and its descendants) into blocks instead of one unbounded rank.
export function componentPositions(graph, edges, mode = 'balanced') {
  const layout = new dagre.graphlib.Graph({ multigraph: true })
    .setGraph({ rankdir: mode === 'vertical' ? 'TB' : 'LR', ranksep: 130, nodesep: 50, marginx: 60, marginy: 60 })
    .setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) layout.setNode(n.name, { width: NODE_WIDTH, height: NODE_HEIGHT });
  edges.forEach((e, i) => layout.setEdge(e.source, e.target, {}, String(i)));
  dagre.layout(layout);
  if (mode !== 'balanced' || graph.nodes.length <= 24) {
    return new Map(graph.nodes.map(n => { const p = layout.node(n.name); return [n.name, { x: p.x - 120, y: p.y - 56 }]; }));
  }
  const children = new Map();
  for (const node of graph.nodes) {
    const siblings = children.get(node.parent) || [];
    siblings.push(node); children.set(node.parent, siblings);
  }
  const roots = graph.nodes.filter(n => !n.parent).sort((a, b) => {
    const p = layout.node(a.name), q = layout.node(b.name);
    return p.x - q.x || p.y - q.y;
  });
  const descendants = node => [node, ...(children.get(node.name) || []).flatMap(descendants)];
  const families = roots.map(descendants).sort((a, b) => b.length - a.length);
  const cellWidth = 310, cellHeight = 180, gap = 60;
  const columnCount = Math.max(4, Math.ceil(Math.sqrt(graph.nodes.length * cellHeight * 1.65 / cellWidth)));
  const skyline = Array(columnCount).fill(60);
  const positions = new Map();
  for (const family of families) {
    const columns = Math.min(4, family.length);
    const height = Math.ceil(family.length / columns) * cellHeight;
    let column = 0, y = Infinity;
    for (let start = 0; start <= columnCount - columns; start++) {
      const top = Math.max(...skyline.slice(start, start + columns));
      if (top < y) { y = top; column = start; }
    }
    const x = 60 + column * cellWidth;
    family.forEach((node, i) => positions.set(node.name, { x: x + (i % columns) * cellWidth, y: y + Math.floor(i / columns) * cellHeight }));
    for (let c = column; c < column + columns; c++) skyline[c] = y + height + gap;
  }
  return positions;
}

export function primaryComponent(graph) {
  const score = new Map(graph.nodes.filter(n => !n.parent).map(n => [n.name, 0]));
  for (const node of graph.nodes) if (node.parent) score.set(node.parent, (score.get(node.parent) || 0) + 1);
  for (const edge of graph.edges) score.set(edge.source, (score.get(edge.source) || 0) + 1);
  return graph.nodes.filter(n => !n.parent).sort((a, b) => (score.get(b.name) || 0) - (score.get(a.name) || 0))[0]?.name;
}
