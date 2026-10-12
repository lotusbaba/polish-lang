import dagre from '@dagrejs/dagre';
import { convertToExcalidrawElements } from '@excalidraw/excalidraw';

export function architectureElements(graph, previous = [], arrange = false) {
  const layout = new dagre.graphlib.Graph({ multigraph: true }).setGraph({ rankdir: 'LR', ranksep: 130, nodesep: 50, marginx: 60, marginy: 60 }).setDefaultEdgeLabel(() => ({}));
  const edges = [...graph.edges, ...graph.nodes.filter(n => n.parent).map(n => ({ source: n.parent, target: n.name, kind: 'contains' }))];
  const old = new Map(previous.filter(e => !e.isDeleted).map(e => [e.id, e]));
  for (const n of graph.nodes) layout.setNode(n.name, { width: 240, height: 112 });
  edges.forEach((e, i) => layout.setEdge(e.source, e.target, {}, String(i)));
  dagre.layout(layout);
  const positions = new Map();
  const shapes = graph.nodes.map(node => {
    const id = `polish:${node.name}`;
    const saved = !arrange && old.get(id);
    const point = layout.node(node.name);
    const x = saved ? saved.x : point.x - 120, y = saved ? saved.y : point.y - 56;
    positions.set(node.name, { x, y });
    const color = /database|table|collection|keyspace|document/.test(node.kind) ? '#e7e0ff' : /compute|cluster|volume/.test(node.kind) ? '#fef0cd' : /frontend|page|cdn/.test(node.kind) ? '#dff2f1' : '#e1edff';
    const product = node.properties.product || node.properties.engine || '';
    return { id, type: 'rectangle', x, y, width: 240, height: 112, backgroundColor: color, fillStyle: 'solid', strokeColor: '#384553', roughness: 1, roundness: { type: 3 },
      label: { text: `${node.name}\n${node.kind}${product ? ' · ' + product : ''}`, fontSize: 17, fontFamily: 2 }, customData: { polish: true, node: node.name } };
  });
  const arrows = edges.map((edge, i) => {
    const a = positions.get(edge.source), b = positions.get(edge.target);
    return { id: `polish-edge:${i}`, type: 'arrow', x: a.x + 240, y: a.y + 56, points: [[0, 0], [b.x - a.x - 240, b.y - a.y]],
      start: { id: `polish:${edge.source}` }, end: { id: `polish:${edge.target}` },
      label: { text: edge.kind + (edge.pattern ? ` ${edge.pattern}` : ''), fontSize: 13, fontFamily: 2 },
      strokeColor: edge.kind === 'contains' ? '#adb5bd' : '#65758b', strokeStyle: edge.kind === 'contains' ? 'dashed' : 'solid', roughness: 0.6,
      customData: { polish: true, relation: edge.kind }, endArrowhead: 'arrow' };
  });
  const generated = convertToExcalidrawElements([...shapes, ...arrows], { regenerateIds: false }).map(e => ({ ...e, customData: { ...e.customData, polish: true } }));
  return [...generated, ...previous.filter(e => !e.customData?.polish)];
}
