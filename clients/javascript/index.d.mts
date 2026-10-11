export type Metric = 'cosine' | 'dot' | 'euclidean' | 'manhattan';
export interface Document {
  id: string; dataset: string; dtype: string; schemaVersion: string;
  rawContent?: string; provenance?: Record<string, unknown>; extensions?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface SearchResult { id: string; metric: Metric; value: number; document: Document; }
export interface Options { cli?: string; }
export interface EmbeddingProfile { format: string; dimensions: number; [key: string]: unknown; }
export class StarVectorError extends Error { code: number | null; }
export class Store {
  constructor(path: string, dimensions: number, options?: Options);
  init(): Promise<{ format: string; dimensions: number }>;
  upsert(document: Document, vector: readonly number[] | Float32Array): Promise<{ upserted: boolean }>;
  get(id: string): Promise<Document>;
  delete(id: string): Promise<boolean>;
  search(vector: readonly number[] | Float32Array, metric?: Metric, limit?: number): Promise<SearchResult[]>;
}
export class DocumentStore {
  constructor(path: string, options?: Options);
  profile(): Promise<EmbeddingProfile>;
  upsert(document: Document): Promise<{ upserted: boolean; id: string }>;
  get(id: string): Promise<Document>;
  delete(id: string): Promise<boolean>;
  search(text: string, metric?: Metric, limit?: number): Promise<SearchResult[]>;
}
