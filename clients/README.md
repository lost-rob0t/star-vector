# Local document and vector clients

Tek9 remains authoritative. The Nim library, C ABI and Common Lisp CFFI retain
their raw-vector APIs. Python adds an in-process C ABI client and reusable text
encoder. Nim, Common Lisp and Node.js/TypeScript offer text convenience calls
through the same local CLI commands. There is no daemon or network service.
The clients are source/package outputs in this repository; they are not
published to PyPI or npm.

## Pinned text package

```sh
nix run .#text -- embedding-profile
nix run .#text -- embed-upsert ./documents ./document.json
printf '%s' 'A kitten rests on a couch.' | nix run .#text -- embed-search ./documents cosine 10 -
```

`document.json` is a canonical StarIntel 0.10.1 document, for example:

```json
{"id":"synthetic-cat","dataset":"synthetic","dtype":"document","schemaVersion":"0.10.1","rawContent":"A cat sleeps on the sofa.","provenance":{"fixture":true}}
```

`embed-upsert STORE DOCUMENT_JSON` and `embed-search STORE METRIC LIMIT QUERY_FILE`
accept `-` for stdin. Results use the existing search JSON shape: `id`, `metric`,
`value`, `document`. Cosine/dot sort descending; Euclidean/Manhattan ascending;
ties sort by ID. Euclidean is the actual distance, not its square. Text limits
may be zero. Raw CLI upserts also accept a document from stdin.

`nix build .#star-vector-text` supplies the CLI and model; the default numeric
package remains lightweight. `.#python-client` is a Python package derivation
for use with `python.withPackages`. Client sources are installed under
`share/star-vector/clients`. The pinned flake checks both supported Linux
architectures, including native interfaces, deterministic profile checks,
TypeScript declarations, and the real-model cross-language proof.

The English encoder imports spaCy **3.8.16** and the official
**en_core_web_md 3.8.0**, with **300 dimensions**. The wheel is fixed by SHA256
`5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310`;
the tokenizer, configuration and vocabulary assets are verified against
`python/star_vector_client/model.json` before loading. The model is a static
word-vector baseline, not a contextual sentence transformer. Its four-document
synthetic demonstration does not establish retrieval quality on your corpus.
See the [official model release](https://github.com/explosion/spacy-models/releases/tag/en_core_web_md-3.8.0)
and [spaCy vector documentation](https://spacy.io/usage/linguistic-features#vectors-similarity).

The encoder takes `rawContent` verbatim, uses the model tokenizer and
`Doc.vector` mean pooling over all tokens, normalizes with a float64 norm, and
stores float32. No case folding, hidden truncation or query prefix is added.
Empty text, an all-zero/out-of-vocabulary vector, and inputs over 1,000,000
Unicode code points are rejected. Large texts should be explicitly represented
as separate canonical chunk documents by the caller; automatic chunking is not
implemented. The profile records extraction/pooling/normalization versions,
dimensions, model and artifact hashes, provider and NumPy versions. The pinned
Nix environment and a pip environment may have different NumPy versions, so
their profiles can differ and cannot silently share a bound store.

## Python: reusable encoder and store

For a source checkout with the native library/worker available:

```sh
python -m pip install ./clients/python
python -m pip install -r clients/python/requirements-text.txt
export STAR_VECTOR_LIBRARY=/absolute/path/libstar_vector.so
export STAR_VECTOR_TEK9_WORKER=/absolute/path/star-vector-tek9-worker
```

Model retrieval is an explicit installation step. Runtime performs no downloads
or paid API calls. `STAR_VECTOR_MODEL` can point at the exact verified unpacked
model assets. The optional text package resolves these paths automatically.

```python
from star_vector_client import DocumentStore, SpacyEncoder

encoder = SpacyEncoder()  # reuse the loaded model
with DocumentStore("./documents", encoder) as store:
    store.upsert(document)  # Python dict; identity and provenance retained
    matches = store.search("A kitten rests on a couch.", "cosine", 10)
    saved = store.get("synthetic-cat")
    saved["rawContent"] = "A cat plays with a toy."
    store.upsert(saved)  # replaces this ID, recomputes vector and text digest
```

`Store(path, dimensions)` provides `upsert(document, vector)`, `get`, `delete`,
`search`, `bind_profile`, and `profile` over the C ABI, without importing spaCy.
Use context managers or call `close`. Serialize calls on each handle; separate
handles may operate concurrently. Custom encoders may supply `profile` and
`encode(text)` through `DocumentStore`; the caller must accurately version that
profile. The supplied production encoder is the pinned spaCy implementation.

## Nim

```nim
import std/json
import star_vector

discard upsertTextDocument("./documents", document)
let matches = searchText("./documents", "A kitten rests on a couch.", "cosine", 10)
```

`textEmbeddingProfile()` returns JSON. These helpers execute
`star-vector-text` on PATH (`STAR_VECTOR_TEXT_HELPER` may specify its absolute
path), passing content through stdin without a shell. Native callers can use
`bindEmbeddingProfile`, `embeddingProfile`, and the optional profile argument
to `upsertDocument` when supplying their own already computed embeddings.

## Common Lisp

Load the existing `star-vector` ASDF system and set `STAR_VECTOR_LIBRARY` for
native CFFI calls. Text helpers use `star-vector` on PATH, or `STAR_VECTOR_CLI`.

```lisp
(star-vector:upsert-text-document "./documents" document-json)
(star-vector:search-text "./documents" "A kitten rests on a couch."
                         :metric :cosine :limit 10)
```

Results are JSON strings, matching the existing CFFI client. Profile-aware
native calls are `bind-embedding-profile`, `embedding-profile`, and
`upsert-embedded-document`. The C header documents the corresponding additive
ABI functions and returned-string ownership.

## JavaScript and TypeScript (Node.js)

Install `./clients/javascript` as a local npm package, or import `index.mjs`
directly. The package includes checked TypeScript declarations and no runtime
dependencies. It needs Node.js 18+ and the local CLI; it is not a browser client.

```typescript
import { DocumentStore, Store } from '@starintel/star-vector';

const documents = new DocumentStore('./documents');
await documents.upsert(document);
const matches = await documents.search('A kitten rests on a couch.', 'cosine', 10);
const saved = await documents.get('synthetic-cat');
// Raw-vector stores also use the same CLI:
const raw = new Store('./raw-vectors', 3);
```

Constructors accept `{cli: '/absolute/path/star-vector'}`; otherwise they use
`STAR_VECTOR_CLI` or PATH. All operations return promises. Child processes use
argument arrays and stdin, not shell commands. Numeric values are checked for
finite dimensions before transfer. `StarVectorError` carries the CLI exit code.

## Compatibility and persistence

The first text open binds an empty Tek9 store to an immutable embedding profile.
Repeated equivalent binds are idempotent. Populated unbound stores, different
profiles, and raw writes into a bound store are rejected. Binding and write
compatibility are checked inside the producer's write transaction, so an older
raw handle cannot bypass a later binding. Deleting every record does not remove
the profile. Numeric reads/searches remain possible for bound stores.
Binding upgrades only that empty store's application header to
`star-vector/tek9/2` with a distinct ready state. Previous versions reject it on
open; previously running v1 workers also reject mutations at their ready-state
check. Use the current library to read or search a profile-bound store. Existing
unbound stores retain their v1 header and behavior.
Binding also disables the legacy migration-publication guard while retaining
the original source fingerprint and checkpoint/count as provenance. Replaying
a completed empty migration performs source/readback checks without undoing the
profile binding; a stale v1 publication attempt fails before changing the store.

Document identity, provenance, revision fields and unrelated extensions survive
text ingestion. The reserved `extensions["star-vector.embedding"]` holds the
profile and SHA256 of the original text; text updates refresh this namespace.
Conflicting unrelated data in that namespace is rejected. Upsert replaces an
existing ID; automatic revision history/as-of search is still a design, not an
implemented feature. Model upgrades require a new empty destination and explicit
reembedding from retained source documents. Existing legacy JSON snapshots stay
read-only and the explicit numeric migration remains source-preserving.

Every acknowledged write uses Tek9's full durability. The deterministic and
real-model suites kill a writer after acknowledgement and verify profile,
document and matching vector after reopen. They do not prove behavior for every
filesystem, device failure or power loss. Convenience text calls load a fresh
encoder process per operation; use Python's reusable library for ingestion
loops. Search remains an exact Tek9 scan with bounded top-k memory; these clients
do not introduce an ANN index or a billion-row capacity claim.
