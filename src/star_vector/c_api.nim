import std/[json, strutils]

import ./[starintel, store]

const
  StarVectorVersion = "0.1.0"
  SvOk = 0'i32
  SvErrorInvalidArgument = 1'i32
  SvErrorInvalidDocument = 2'i32
  SvErrorNotFound = 3'i32
  SvErrorIo = 4'i32
  SvErrorInternal = 5'i32

type StoreHandle = object
  store: VectorStore
  lastError: string

var globalLastError {.threadvar.}: string

proc setError(handle: ptr StoreHandle, status: int32, message: string): int32 =
  if handle.isNil:
    globalLastError = message
  else:
    handle.lastError = message
  status

proc clearError(handle: ptr StoreHandle) =
  if handle.isNil:
    globalLastError.setLen(0)
  else:
    handle.lastError.setLen(0)

proc checkedLength(value: csize_t, label: string): int =
  if uint64(value) > uint64(high(int)):
    raise newException(ValueError, label & " is too large")
  int(value)

proc copyVector(values: ptr cfloat, count: csize_t): seq[float32] =
  let length = checkedLength(count, "vector length")
  if length > 0 and values.isNil:
    raise newException(ValueError, "vector pointer is null")
  let source = cast[ptr UncheckedArray[cfloat]](values)
  result = newSeq[float32](length)
  for index in 0 ..< length:
    result[index] = source[index].float32

proc copyCString(value: string): cstring =
  let memory = cast[ptr UncheckedArray[char]](allocShared0(value.len + 1))
  if value.len > 0:
    copyMem(addr memory[0], unsafeAddr value[0], value.len)
  cast[cstring](memory)

proc metricFromC(value: cint): VectorMetric =
  case value
  of 0: vmCosine
  of 1: vmDot
  of 2: vmEuclidean
  of 3: vmManhattan
  else: raise newException(ValueError, "unsupported vector metric: " & $value)

proc metricName(value: VectorMetric): string =
  case value
  of vmCosine: "cosine"
  of vmDot: "dot"
  of vmEuclidean: "euclidean"
  of vmManhattan: "manhattan"

proc storeErrorStatus(message: string): int32 =
  if message.startsWith("invalid StarIntel document"):
    SvErrorInvalidDocument
  elif message.startsWith("document not found"):
    SvErrorNotFound
  elif "dimension" in message or "vector" in message or "limit" in message or
       "dimensions" in message:
    SvErrorInvalidArgument
  else:
    SvErrorInternal

proc sv_version(): cstring {.exportc, cdecl, dynlib.} =
  StarVectorVersion.cstring

proc sv_open(
  path: cstring,
  dimensions: csize_t,
  outStore: ptr pointer
): int32 {.exportc, cdecl, dynlib.} =
  if outStore.isNil:
    return setError(nil, SvErrorInvalidArgument, "out_store pointer is null")
  outStore[] = nil
  try:
    let dimensionCount = checkedLength(dimensions, "dimensions")
    let storePath = if path.isNil: "" else: $path
    let opened = openVectorStore(storePath, dimensionCount)
    let handle = create(StoreHandle)
    handle.store = opened
    handle.lastError = ""
    outStore[] = cast[pointer](handle)
    clearError(nil)
    SvOk
  except ValueError as error:
    setError(nil, SvErrorInvalidArgument, error.msg)
  except VectorStoreError as error:
    setError(nil, storeErrorStatus(error.msg), error.msg)
  except OSError as error:
    setError(nil, SvErrorIo, error.msg)
  except CatchableError as error:
    setError(nil, SvErrorInternal, error.msg)

proc sv_close(storePointer: pointer): int32 {.exportc, cdecl, dynlib.} =
  if storePointer.isNil:
    return setError(nil, SvErrorInvalidArgument, "store pointer is null")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    handle.store.close()
    handle.store = nil
    handle.lastError.setLen(0)
    dealloc(cast[pointer](handle))
    SvOk
  except OSError as error:
    setError(handle, SvErrorIo, error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_flush(storePointer: pointer): int32 {.exportc, cdecl, dynlib.} =
  if storePointer.isNil:
    return setError(nil, SvErrorInvalidArgument, "store pointer is null")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    handle.store.flush()
    clearError(handle)
    SvOk
  except OSError as error:
    setError(handle, SvErrorIo, error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_dimensions(storePointer: pointer): csize_t {.exportc, cdecl, dynlib.} =
  if storePointer.isNil:
    discard setError(nil, SvErrorInvalidArgument, "store pointer is null")
    return 0
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    result = csize_t(handle.store.dimensions)
    clearError(handle)
  except CatchableError as error:
    discard setError(handle, SvErrorInternal, error.msg)

proc sv_len(storePointer: pointer): csize_t {.exportc, cdecl, dynlib.} =
  if storePointer.isNil:
    discard setError(nil, SvErrorInvalidArgument, "store pointer is null")
    return 0
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    result = csize_t(handle.store.len)
    clearError(handle)
  except CatchableError as error:
    discard setError(handle, SvErrorInternal, error.msg)

proc sv_upsert_document(
  storePointer: pointer,
  documentJson: cstring,
  vector: ptr cfloat,
  vectorLength: csize_t
): int32 {.exportc, cdecl, dynlib.} =
  if storePointer.isNil or documentJson.isNil:
    return setError(nil, SvErrorInvalidArgument, "store and document pointers are required")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    let parsed = parseStarIntelDocument($documentJson)
    if not parsed.validation.ok:
      return setError(
        handle,
        SvErrorInvalidDocument,
        parsed.validation.category & ": " & parsed.validation.message
      )
    let copied = copyVector(vector, vectorLength)
    handle.store.upsertDocument(parsed.document, copied)
    clearError(handle)
    SvOk
  except ValueError as error:
    setError(handle, SvErrorInvalidArgument, error.msg)
  except VectorStoreError as error:
    setError(handle, storeErrorStatus(error.msg), error.msg)
  except OSError as error:
    setError(handle, SvErrorIo, error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_get_document(
  storePointer: pointer,
  id: cstring,
  outDocumentJson: ptr cstring
): int32 {.exportc, cdecl, dynlib.} =
  if outDocumentJson.isNil:
    return setError(nil, SvErrorInvalidArgument, "out_document_json pointer is null")
  outDocumentJson[] = nil
  if storePointer.isNil or id.isNil:
    return setError(nil, SvErrorInvalidArgument, "store and id pointers are required")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    outDocumentJson[] = copyCString($handle.store.getDocument($id))
    clearError(handle)
    SvOk
  except VectorStoreError as error:
    setError(handle, storeErrorStatus(error.msg), error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_delete_document(
  storePointer: pointer,
  id: cstring,
  outDeleted: ptr int32
): int32 {.exportc, cdecl, dynlib.} =
  if storePointer.isNil or id.isNil or outDeleted.isNil:
    return setError(nil, SvErrorInvalidArgument, "store, id, and out_deleted pointers are required")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    outDeleted[] = if handle.store.deleteDocument($id): 1'i32 else: 0'i32
    clearError(handle)
    SvOk
  except OSError as error:
    setError(handle, SvErrorIo, error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_search_json(
  storePointer: pointer,
  query: ptr cfloat,
  queryLength: csize_t,
  metricValue: cint,
  limit: csize_t,
  outResultsJson: ptr cstring
): int32 {.exportc, cdecl, dynlib.} =
  if outResultsJson.isNil:
    return setError(nil, SvErrorInvalidArgument, "out_results_json pointer is null")
  outResultsJson[] = nil
  if storePointer.isNil:
    return setError(nil, SvErrorInvalidArgument, "store pointer is null")
  let handle = cast[ptr StoreHandle](storePointer)
  try:
    let metric = metricFromC(metricValue)
    let copied = copyVector(query, queryLength)
    let searchLimit = checkedLength(limit, "search limit")
    var encoded = newJArray()
    for item in handle.store.search(copied, metric, searchLimit):
      encoded.add(%*{
        "id": item.id,
        "metric": metricName(metric),
        "value": item.value,
        "document": item.document
      })
    outResultsJson[] = copyCString($encoded)
    clearError(handle)
    SvOk
  except ValueError as error:
    setError(handle, SvErrorInvalidArgument, error.msg)
  except VectorStoreError as error:
    setError(handle, storeErrorStatus(error.msg), error.msg)
  except CatchableError as error:
    setError(handle, SvErrorInternal, error.msg)

proc sv_last_error(storePointer: pointer): cstring {.exportc, cdecl, dynlib.} =
  if storePointer.isNil:
    globalLastError.cstring
  else:
    cast[ptr StoreHandle](storePointer).lastError.cstring

proc sv_string_free(value: cstring) {.exportc, cdecl, dynlib.} =
  if not value.isNil:
    deallocShared(cast[pointer](value))
