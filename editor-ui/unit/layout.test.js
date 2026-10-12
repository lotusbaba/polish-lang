import { test } from 'node:test';
import assert from 'node:assert/strict';
import { componentPositions, primaryComponent } from '../src/layout.js';

const nodes = Array.from({ length: 10 }, (_, i) => ({ name: `Service${i}`, parent: null, kind: 'service' }));
for (let i = 0; i < 100; i++) nodes.push({ name: `Service${i % 10}.op${i}`, parent: `Service${i % 10}`, kind: 'operation' });
const edges = nodes.filter(n => n.parent).map(n => ({ source: n.parent, target: n.name }));
const graph = { nodes, edges: [] };
test('large balanced layout preserves every component without overlap or a narrow strip', () => {
  const positions = componentPositions(graph, edges);
  assert.equal(positions.size, nodes.length);
  assert.deepEqual(positions, componentPositions(graph, edges));
  const points = [...positions.values()];
  for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
    const a = points[i], b = points[j];
    assert.ok(Math.abs(a.x - b.x) >= 240 || Math.abs(a.y - b.y) >= 112);
  }
  const width = Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x)) + 240;
  const height = Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y)) + 112;
  assert.ok(width / height > 0.8 && width / height < 2.5);
  assert.equal(primaryComponent(graph), 'Service0');
});
test('direction changes rank orientation', () => {
  const small = { nodes: [{ name: 'A' }, { name: 'B' }] };
  const links = [{ source: 'A', target: 'B' }];
  const h = componentPositions(small, links, 'horizontal'), v = componentPositions(small, links, 'vertical');
  assert.ok(h.get('B').x > h.get('A').x);
  assert.ok(v.get('B').y > v.get('A').y);
});
