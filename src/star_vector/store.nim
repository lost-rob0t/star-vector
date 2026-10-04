import std/[algorithm, json, locks, math, os, tables]

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

proc clonedRecords(source: Table[string, VectorRecord]): Table[string, VectorRecord] =
  result = initTable[string, VectorRecord](source.len)
  for id, record in source.pairs:
    result[id] = record

proc encodedSnapshot(dimensions: int, records: Table[string, VectorRecord]): string =
  var ids: seq[string]
  for id in records.keys:
    ids.add(id)
  ids.sort()

  var encodedRecords = newJArray()
  for id in ids:
    let record = records[id]
    var vector = newJArray()
    for value in record.vector:
      vector.add(%value)
    encodedRecords.add(%*{
      "document": record.document,
      "vector": vector
    })

  $(%*{
    "format": StoreFormat,
    "dimensions": dimensions,
    "records": encodedRecords
  }) & "\n"

proc persist(path: string, dimensions: int, records: Table[string, VectorRecord]) =
  if path.len == 0:
    return
  let parent = path.parentDir
  if parent.len > 0:
    createDir(parent)
  let temporary = path & ".tmp-" & $getCurrentProcessId()
  try:
    writeFile(temporary, encodedSnapshot(dimensions, records))
    moveFile(temporary, path)
  except CatchableError:
    if fileExists(temporary):
      removeFile(temporary)
    raise

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
    if encoded.kind != JObject or not encoded.hasKey("document") or
       not encoded.hasKey("vector") or encoded["vector"].kind != JArray:
      fail("vector store contains a corrupt record")
    let checked = validateStarIntelDocument(encoded["document"])
    if not checked.ok:
      fail("stored StarIntel document is invalid: " & checked.message)
    var vector: seq[float32]
    for value in encoded["vector"].items:
      if value.kind notin {JInt, JFloat}:
        fail("stored vector contains a non-number")
      vector.add(numericValue(value).float32)
    validateVector(vector, dimensions)
    if result.hasKey(checked.id):
      fail("vector store contains duplicate document ID " & checked.id)
    result[checked.id] = VectorRecord(document: cloneDocument(encoded["document"]), vector: vector)

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
    result = store.records.len
  finally:
    release(store.lock)

proc flush*(store: VectorStore) =
  store.requireOpen()
  acquire(store.lock)
  try:
    persist(store.path, store.dimensionCount, store.records)
  finally:
    release(store.lock)

proc close*(store: VectorStore) =
  if store.isNil or store.closed:
    return
  acquire(store.lock)
  try:
    persist(store.path, store.dimensionCount, store.records)
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
  let record = VectorRecord(document: cloneDocument(document), vector: @vector)

  acquire(store.lock)
  try:
    var candidate = clonedRecords(store.records)
    candidate[checked.id] = record
    persist(store.path, store.dimensionCount, candidate)
    store.records = move(candidate)
  finally:
    release(store.lock)

proc getDocument*(store: VectorStore, id: string): JsonNode =
  store.requireOpen()
  acquire(store.lock)
  try:
    if not store.records.hasKey(id):
      fail("document not found: " & id)
    result = cloneDocument(store.records[id].document)
  finally:
    release(store.lock)

proc deleteDocument*(store: VectorStore, id: string): bool =
  store.requireOpen()
  acquire(store.lock)
  try:
    if not store.records.hasKey(id):
      return false
    var candidate = clonedRecords(store.records)
    candidate.del(id)
    persist(store.path, store.dimensionCount, candidate)
    store.records = move(candidate)
    result = true
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

proc search*(
  store: VectorStore,
  query: openArray[float32],
  metric: VectorMetric,
  limit: int
): seq[SearchResult] =
  store.requireOpen()
  validateVector(query, store.dimensionCount)
  if limit < 0:
    fail("search limit cannot be negative")
  if limit == 0:
    return
  let queryNorm = norm(query)
  if metric == vmCosine and queryNorm == 0.0:
    fail("cosine similarity requires a non-zero query vector")

  acquire(store.lock)
  try:
    for id, record in store.records.pairs:
      if metric == vmCosine and norm(record.vector) == 0.0:
        continue
      result.add(SearchResult(
        id: id,
        value: metricValue(query, record.vector, metric),
        document: cloneDocument(record.document)
      ))
  finally:
    release(store.lock)

  result.sort(proc(left, right: SearchResult): int =
    if metric in {vmCosine, vmDot}:
      result = cmp(right.value, left.value)
    else:
      result = cmp(left.value, right.value)
    if result == 0:
      result = cmp(left.id, right.id)
  )
  if result.len > limit:
    result.setLen(limit)
