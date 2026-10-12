import { componentPositions } from './layout';
import { convertToExcalidrawElements } from '@excalidraw/excalidraw';

export function architectureElements(graph, previous = [], arrange = false, mode = 'balanced') {
  const edges = [...graph.edges, ...graph.nodes.filter(n => n.parent).map(n => ({ source: n.parent, target: n.name, kind: 'contains' }))];
  const old = new Map(previous.filter(e => !e.isDeleted).map(e => [e.id, e]));
  const layout = componentPositions(graph, edges, mode);
  const positions = new Map();
  const shapes = graph.nodes.map(node => {
    const id = `polish:${node.name}`;
    const saved = !arrange && old.get(id);
    const point = layout.get(node.name);
    const x = saved ? saved.x : point.x, y = saved ? saved.y : point.y;
    positions.set(node.name, { x, y });
    const color = /database|table|collection|keyspace|document/.test(node.kind) ? '#e7e0ff' : /compute|cluster|volume/.test(node.kind) ? '#fef0cd' : /frontend|page|cdn/.test(node.kind) ? '#dff2f1' : '#e1edff';
    const product = node.properties.product || node.properties.engine || '';
    return { id, type: 'rectangle', x, y, width: 240, height: 112, backgroundColor: color, fillStyle: 'solid', strokeColor: '#384553', roughness: 1, roundness: { type: 3 },
      label: { text: `${node.name}\n${node.kind}${product ? ' · ' + product : ''}`, fontSize: 17, fontFamily: 2 }, customData: { polish: true, node: node.name } };
  });
  const arrows = edges.map((edge, i) => {
    const a = positions.get(edge.source), b = positions.get(edge.target);
    const dx = b.x - a.x, dy = b.y - a.y;
    const horizontal = Math.abs(dx) > Math.abs(dy);
    const start = horizontal ? [a.x + (dx >= 0 ? 240 : 0), a.y + 56] : [a.x + 120, a.y + (dy >= 0 ? 112 : 0)];
    const end = horizontal ? [b.x + (dx >= 0 ? 0 : 240), b.y + 56] : [b.x + 120, b.y + (dy >= 0 ? 0 : 112)];
    return { id: `polish-edge:${i}`, type: 'arrow', x: start[0], y: start[1], points: [[0, 0], [end[0] - start[0], end[1] - start[1]]],
      start: { id: `polish:${edge.source}` }, end: { id: `polish:${edge.target}` },
      ...(edge.kind === 'contains' && graph.nodes.length > 24 ? { opacity: 25 } : { label: { text: edge.kind + (edge.pattern ? ` ${edge.pattern}` : ''), fontSize: 13, fontFamily: 2 } }),
      strokeColor: edge.kind === 'contains' ? '#adb5bd' : '#65758b', strokeStyle: edge.kind === 'contains' ? 'dashed' : 'solid', roughness: 0.6,
      customData: { polish: true, relation: edge.kind }, endArrowhead: 'arrow' };
  });
  const generated = convertToExcalidrawElements([...shapes, ...arrows], { regenerateIds: false }).map(e => ({ ...e, customData: { ...e.customData, polish: true } }));
  const isConnector = e => e.type === 'arrow' || e.containerId?.startsWith('polish-edge:');
  // Paint connectors below component cards so crossing lines do not obscure names.
  return [...generated.filter(isConnector), ...generated.filter(e => !isConnector(e)), ...previous.filter(e => !e.customData?.polish)];
}
