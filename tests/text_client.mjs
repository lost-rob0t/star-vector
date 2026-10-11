import assert from 'node:assert/strict';
import { Store, DocumentStore } from '../clients/javascript/index.mjs';

const path = process.argv[2];
const store = new DocumentStore(path);
const document = await store.get('synthetic-shared');
assert.equal(document.rev, 'nim');
document.rev = 'javascript';
document.rawContent = 'A telescope observes distant stars and galaxies.';
await store.upsert(document);
for (const metric of ['cosine', 'dot', 'euclidean', 'manhattan'])
  assert.equal((await store.search(document.rawContent, metric, 1))[0].id, 'synthetic-shared');
await assert.rejects(new Store(path, 300).upsert(document, new Float32Array(300).fill(1)), /profile mismatch/i);
await assert.rejects(store.get('absent-synthetic'), /document not found/);
assert.throws(() => new Store(path, 3).search([1, 2, NaN]), /finite vector/);
assert.deepEqual(await new Store(path, 300).search(new Float32Array(300).fill(1), 'cosine', 0), []);
console.log('JavaScript text client passed');
