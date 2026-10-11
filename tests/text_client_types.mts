import { DocumentStore, Store, type Document, type SearchResult } from '../clients/javascript/index.mjs';

const document: Document = { id: 'synthetic', dataset: 'synthetic', dtype: 'document', schemaVersion: '0.10.1', rawContent: 'A cat sleeps.' };
const text = new DocumentStore('/tmp/synthetic-types-only');
const results: Promise<SearchResult[]> = text.search('A kitten rests.', 'cosine', 5);
void results;
void text.upsert(document);
void new Store('/tmp/synthetic-types-only', 3).upsert(document, new Float32Array([1, 2, 3]));
// @ts-expect-error The public metric union catches misspelled metrics.
void text.search('query', 'cosign');
// @ts-expect-error Dimensions must be numeric.
new Store('/tmp/synthetic-types-only', 'three');
