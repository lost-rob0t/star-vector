import std/[algorithm, heapqueue, json, locks, math, os, sha1, strutils, tables]

import ./tek9_backend

import ./starintel

type
  VectorStoreError* = object of CatchableError

  VectorMetric* = enum
    vmCosine
    vmDot
    vmEuclidean
    vmManhattan

  VectorRecord = object
    document: JsonNode
    vector: seq[float32]

  SearchResult* = object
    id*: string
    value*: float64
    document*: JsonNode

  VectorStore* = ref object
    lock: Lock
    path: string
    dimensionCount: int
    records: Table[string, VectorRecord]
    closed: bool
    legacy: bool
    backend: Tek9Backend

const StoreFormat = "star-vector/1"

proc fail(message: string) {.noreturn.} =
  raise newException(VectorStoreError, message)

proc finite(vector: openArray[float32]): bool =
  for value in vector:
    if (cast[uint32](value) and 0x7f800000'u32) == 0x7f800000'u32:
      return false
  true

proc validateVector(vector: openArray[float32], dimensions: int) =
  if vector.len != dimensions:
    fail("vector dimension mismatch: expected " & $dimensions & ", got " & $vector.len)
  if not finite(vector):
    fail("vector contains a non-finite value")

proc cloneDocument(document: JsonNode): JsonNode =
  parseJson($document)

proc storageKey(id: string): string =
  # Canonical IDs allow 512 ASCII bytes; LMDB keys allow at most 511.
  # '#' is outside the canonical ID alphabet, isolating the long-ID namespace.
  if id.len <= 511: id else: "#" & $secureHash(id)

proc encodeRecord(record: VectorRecord): string =
  var vector = newJArray()
  for value in record.vector: vector.add(%value)
  $(%*{"document": record.document, "vector": vector})

proc decodeRecord(encoded: JsonNode, dimensions: int): VectorRecord =
  if encoded.kind != JObject or not encoded.hasKey("document") or
     not encoded.hasKey("vector") or encoded["vector"].kind != JArray:
    fail("vector store contains a corrupt record")
  let checked = validateStarIntelDocument(encoded["document"])
  if not checked.ok:
    fail("stored StarIntel document is invalid: " & checked.message)
  for value in encoded["vector"].items:
    if value.kind notin {JInt, JFloat}:
      fail("stored vector contains a non-number")
    result.vector.add(numericValue(value).float32)
  validateVector(result.vector, dimensions)
  result.document = encoded["document"]

proc loadRecords(path: string, dimensions: int): Table[string, VectorRecord] =
  result = initTable[string, VectorRecord]()
  let snapshot = parseFile(path)
  if snapshot.kind != JObject or not snapshot.hasKey("format") or
     snapshot["format"].kind != JString or snapshot["format"].getStr != StoreFormat:
    fail("unsupported or corrupt vector store format")
  if not snapshot.hasKey("dimensions") or snapshot["dimensions"].kind != JInt:
    fail("vector store is missing dimensions")
  let storedDimensions = snapshot["dimensions"].getInt
  if storedDimensions != dimensions:
    fail("vector store dimension mismatch: expected " & $storedDimensions & ", got " & $dimensions)
  if not snapshot.hasKey("records") or snapshot["records"].kind != JArray:
    fail("vector store is missing records")

  for encoded in snapshot["records"].items:
    let record = decodeRecord(encoded, dimensions)
    let id = record.document["id"].getStr
    if result.hasKey(id): fail("vector store contains duplicate document ID " & id)
    result[id] = record

proc openVectorStore*(path: string, dimensions: int): VectorStore =
  if dimensions <= 0:
    fail("vector store dimensions must be positive")
  new(result)
  initLock(result.lock)
  result.path = path
  result.dimensionCount = dimensions
  try:
    if path.len > 0 and fileExists(path):
      result.records = loadRecords(path, dimensions)
      result.legacy = true
    elif path.len > 0:
      try:
        result.backend = openTek9(path, dimensions)
      except IOError as error:
        if "dimension" in error.msg: fail(error.msg)
        raise
    else:
      result.records = initTable[string, VectorRecord]()
  except CatchableError:
    deinitLock(result.lock)
    raise

proc requireOpen(store: VectorStore) =
  if store.isNil or store.closed:
    fail("vector store is closed")

proc dimensions*(store: VectorStore): int =
  store.requireOpen()
  store.dimensionCount

proc len*(store: VectorStore): int =
  store.requireOpen()
  acquire(store.lock)
  try:
    if store.backend.isNil:
      result = store.records.len
    else:
      result = store.backend.request(%*{"op": "status"})["count"].getInt
  finally:
    release(store.lock)

proc flush*(store: VectorStore) =
  store.requireOpen()
  acquire(store.lock)
  try:
    if not store.backend.isNil:
      discard store.backend.request(%*{"op": "flush"})
  finally:
    release(store.lock)

proc close*(store: VectorStore) =
  if store.isNil or store.closed:
    return
  acquire(store.lock)
  try:
    if not store.backend.isNil: store.backend.close()
    store.closed = true
  finally:
    release(store.lock)
  deinitLock(store.lock)

proc upsertDocument*(store: VectorStore, document: JsonNode, vector: openArray[float32]) =
  store.requireOpen()
  let checked = validateStarIntelDocument(document)
  if not checked.ok:
    fail("invalid StarIntel document (" & checked.category & "): " & checked.message)
  validateVector(vector, store.dimensionCount)
  if store.legacy: fail("legacy snapshot is read-only; use explicit migrate to a new destination")
  let record = VectorRecord(document: cloneDocument(document), vector: @vector)

  acquire(store.lock)
  try:
    if store.backend.isNil:
      store.records[checked.id] = record
    else:
      discard store.backend.request(%*{"op": "upsert", "id": storageKey(checked.id), "record": encodeRecord(record)})
  finally:
    release(store.lock)

proc getDocument*(store: VectorStore, id: string): JsonNode =
  store.requireOpen()
  acquire(store.lock)
  try:
    if store.backend.isNil:
      if not store.records.hasKey(id): fail("document not found: " & id)
      result = cloneDocument(store.records[id].document)
    else:
      let response = store.backend.request(%*{"op": "get", "id": storageKey(id)})
      if response["record"].kind == JNull: fail("document not found: " & id)
      result = decodeRecord(parseJson(response["record"].getStr), store.dimensionCount).document
      if result["id"].getStr != id: fail("document not found: " & id)
  finally:
    release(store.lock)

proc deleteDocument*(store: VectorStore, id: string): bool =
  store.requireOpen()
  if store.legacy: fail("legacy snapshot is read-only; use explicit migrate to a new destination")
  acquire(store.lock)
  try:
    if store.backend.isNil:
      result = store.records.hasKey(id)
      store.records.del(id)
    else:
      result = store.backend.request(%*{"op": "delete", "id": storageKey(id), "documentId": id})["deleted"].getBool
  finally:
    release(store.lock)

proc dotProduct(left, right: openArray[float32]): float64 =
  for index in 0 ..< left.len:
    result += left[index].float64 * right[index].float64

proc norm(vector: openArray[float32]): float64 =
  sqrt(dotProduct(vector, vector))

proc metricValue(left, right: openArray[float32], metric: VectorMetric): float64 =
  case metric
  of vmCosine:
    result = dotProduct(left, right) / (norm(left) * norm(right))
  of vmDot:
    result = dotProduct(left, right)
  of vmEuclidean:
    for index in 0 ..< left.len:
      let delta = left[index].float64 - right[index].float64
      result += delta * delta
    result = sqrt(result)
  of vmManhattan:
    for index in 0 ..< left.len:
      result += abs(left[index].float64 - right[index].float64)

type RankedResult = object
  rank: float64
  item: SearchResult

proc `<`(left, right: RankedResult): bool =
  # Min-heap root is the worst retained candidate, including ID ties.
  left.rank > right.rank or (left.rank == right.rank and left.item.id > right.item.id)

proc search*(store: VectorStore, query: openArray[float32], metric: VectorMetric,
             limit: int): seq[SearchResult] =
  store.requireOpen()
  validateVector(query, store.dimensionCount)
  if limit < 0: fail("search limit cannot be negative")
  if limit == 0: return
  if metric == vmCosine and norm(query) == 0.0:
    fail("cosine similarity requires a non-zero query vector")
  var best = initHeapQueue[RankedResult]()
  template consider(recordId: string, record: VectorRecord) =
    if metric != vmCosine or norm(record.vector) != 0.0:
      let value = metricValue(query, record.vector, metric)
      let rank = if metric in {vmCosine, vmDot}: -value else: value
      if best.len < limit or rank < best[0].rank or
         (rank == best[0].rank and recordId < best[0].item.id):
        let candidate = RankedResult(rank: rank,
          item: SearchResult(id: recordId, value: value, document: record.document))
        if best.len == limit: discard best.pop()
        best.push(candidate)
  acquire(store.lock)
  try:
    if store.backend.isNil:
      for id, record in store.records.pairs: consider(id, record)
    else:
      store.backend.send(%*{"op": "scan"})
      try:
        while true:
          let response = store.backend.receive()
          if response.hasKey("end"): break
          let record = decodeRecord(parseJson(response["record"].getStr), store.dimensionCount)
          let id = record.document["id"].getStr
          if storageKey(id) != response["id"].getStr: fail("corrupt vector store record ID")
          consider(id, record)
      except CatchableError:
        # A failed scan must never leave unread rows to corrupt the next response.
        store.backend.stop()
        raise
    result = newSeq[SearchResult](best.len)
    for index in countdown(best.len - 1, 0):
      result[index] = best.pop().item
      if store.backend.isNil: result[index].document = cloneDocument(result[index].document)
  finally:
    release(store.lock)

proc migrateSnapshot*(source, destination: string, dimensions: int,
                      maxRecords = high(int)): JsonNode =
  if dimensions <= 0 or maxRecords < 0: fail("invalid migration dimensions or limit")
  if not fileExists(source): fail("legacy snapshot source must be an existing file")
  if fileExists(destination) or absolutePath(source) == absolutePath(destination):
    fail("migration destination must be a separate Tek9 directory")
  # Legacy reader intentionally validates every record before destination creation.
  # Its whole-snapshot memory cost is retained and documented, not hidden.
  let fingerprint = $secureHashFile(source)
  let records = loadRecords(source, dimensions)
  if $secureHashFile(source) != fingerprint: fail("migration source changed during validation")
  var ids: seq[string]
  for id in records.keys: ids.add(id)
  ids.sort()
  let backend = openTek9(destination, dimensions, fingerprint, ids.len)
  defer: backend.close()
  let status = backend.request(%*{"op": "status"})
  let start = status["next"].getInt
  if start < 0 or start > ids.len: fail("invalid migration checkpoint")
  let stop = start + min(maxRecords, ids.len - start)
  if not status["complete"].getBool:
    var index = start
    while index < stop:
      let expected = index
      var batch = newJArray()
      var bytes = 0
      while index < stop and batch.len < 128:
        let id = ids[index]
        let encoded = encodeRecord(records[id])
        if batch.len > 0 and bytes + encoded.len > 1024 * 1024: break
        batch.add(%*{"id": storageKey(id), "record": encoded})
        bytes += encoded.len
        inc index
      discard backend.request(%*{"op": "migrate-batch", "expected": expected, "records": batch})
  if stop == ids.len:
    for id in ids:
      let response = backend.request(%*{"op": "get", "id": storageKey(id)})
      if response["record"].kind != JString or
         parseJson(response["record"].getStr) != parseJson(encodeRecord(records[id])):
        fail("migration readback mismatch: " & id)
    if backend.request(%*{"op": "status"})["count"].getInt != ids.len:
      fail("migration readback count mismatch")
    if $secureHashFile(source) != fingerprint: fail("migration source changed before publication")
    discard backend.request(%*{"op": "publish"})
  result = %*{"complete": stop == ids.len, "checkpoint": stop,
    "total": ids.len, "sourceFingerprint": fingerprint, "destination": destination}
