import { spawn } from 'node:child_process';

const metrics = new Set(['cosine', 'dot', 'euclidean', 'manhattan']);

export class StarVectorError extends Error {
  constructor(message, code) { super(message); this.name = 'StarVectorError'; this.code = code; }
}

function command(cli, args, input = '') {
  return new Promise((resolve, reject) => {
    const child = spawn(cli, args.map(String), { stdio: ['pipe', 'pipe', 'pipe'], shell: false });
    let stdout = '', stderr = '', inputError;
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    child.on('error', reject);
    child.stdin.on('error', error => { inputError = error; });
    child.on('close', code => {
      if (code !== 0) return reject(new StarVectorError(stderr.trim() || 'Star Vector command failed', code));
      if (inputError) return reject(inputError);
      try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); }
    });
    child.stdin.end(input, 'utf8');
  });
}

function checkSearch(metric, limit) {
  if (!metrics.has(metric)) throw new TypeError('unsupported vector metric');
  if (!Number.isSafeInteger(limit) || limit < 0) throw new TypeError('limit must be a non-negative integer');
}

export class Store {
  constructor(path, dimensions, { cli = process.env.STAR_VECTOR_CLI || 'star-vector' } = {}) {
    if (!Number.isSafeInteger(dimensions) || dimensions <= 0) throw new TypeError('dimensions must be positive');
    this.path = path; this.dimensions = dimensions; this.cli = cli;
  }
  init() { return command(this.cli, ['init', this.path, this.dimensions]); }
  vector(values) {
    if (values.length !== this.dimensions || !Array.from(values).every(Number.isFinite))
      throw new TypeError('expected a finite vector with the store dimensions');
    return Array.from(values).join(',');
  }
  upsert(document, vector) {
    return command(this.cli, ['upsert', this.path, this.dimensions, '-', this.vector(vector)], JSON.stringify(document));
  }
  get(id) { return command(this.cli, ['get', this.path, this.dimensions, id]); }
  async delete(id) { return (await command(this.cli, ['delete', this.path, this.dimensions, id])).deleted; }
  search(vector, metric = 'cosine', limit = 10) {
    checkSearch(metric, limit);
    return command(this.cli, ['search', this.path, this.dimensions, metric, limit, this.vector(vector)]);
  }
}

export class DocumentStore {
  constructor(path, { cli = process.env.STAR_VECTOR_CLI || 'star-vector' } = {}) {
    this.path = path; this.cli = cli;
  }
  profile() { return command(this.cli, ['embedding-profile']); }
  upsert(document) { return command(this.cli, ['embed-upsert', this.path, '-'], JSON.stringify(document)); }
  search(text, metric = 'cosine', limit = 10) {
    checkSearch(metric, limit);
    return command(this.cli, ['embed-search', this.path, metric, limit, '-'], text);
  }
  async get(id) { const p = await this.profile(); return new Store(this.path, p.dimensions, this).get(id); }
  async delete(id) { const p = await this.profile(); return new Store(this.path, p.dimensions, this).delete(id); }
}
