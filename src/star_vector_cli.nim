import std/[json, os, strutils]

import star_vector

proc usage() {.noreturn.} =
  stderr.writeLine("""usage:
  star-vector init STORE DIMENSIONS
  star-vector upsert STORE DIMENSIONS DOCUMENT_JSON VECTOR
  star-vector get STORE DIMENSIONS DOCUMENT_ID
  star-vector delete STORE DIMENSIONS DOCUMENT_ID
  star-vector search STORE DIMENSIONS METRIC LIMIT VECTOR

VECTOR is a comma-separated list of numbers. METRIC is cosine, dot,
euclidean, or manhattan.""")
  quit(2)

proc positiveInteger(value, label: string): int =
  try:
    result = parseInt(value)
  except ValueError:
    raise newException(ValueError, label & " must be an integer")
  if result <= 0:
    raise newException(ValueError, label & " must be positive")

proc parseVector(value: string): seq[float32] =
  if value.len == 0:
    raise newException(ValueError, "vector cannot be empty")
  for item in value.split(','):
    try:
      result.add(parseFloat(item).float32)
    except ValueError:
      raise newException(ValueError, "invalid vector value: " & item)

proc parseMetric(value: string): VectorMetric =
  case value
  of "cosine": vmCosine
  of "dot": vmDot
  of "euclidean": vmEuclidean
  of "manhattan": vmManhattan
  else: raise newException(ValueError, "unsupported metric: " & value)

proc metricName(metric: VectorMetric): string =
  case metric
  of vmCosine: "cosine"
  of vmDot: "dot"
  of vmEuclidean: "euclidean"
  of vmManhattan: "manhattan"

proc encodedResults(results: seq[SearchResult], metric: VectorMetric): JsonNode =
  result = newJArray()
  for item in results:
    result.add(%*{
      "id": item.id,
      "metric": metricName(metric),
      "value": item.value,
      "document": item.document
    })

proc main() =
  let arguments = commandLineParams()
  if arguments.len < 3:
    usage()

  let command = arguments[0]
  let path = arguments[1]
  let dimensions = positiveInteger(arguments[2], "dimensions")
  let store = openVectorStore(path, dimensions)
  defer: store.close()

  case command
  of "init":
    if arguments.len != 3:
      usage()
    store.flush()
    echo $(%*{"format": "star-vector/1", "dimensions": dimensions})
  of "upsert":
    if arguments.len != 5:
      usage()
    store.upsertDocument(parseFile(arguments[3]), parseVector(arguments[4]))
    echo $(%*{"upserted": true})
  of "get":
    if arguments.len != 4:
      usage()
    echo $store.getDocument(arguments[3])
  of "delete":
    if arguments.len != 4:
      usage()
    echo $(%*{"deleted": store.deleteDocument(arguments[3])})
  of "search":
    if arguments.len != 6:
      usage()
    let metric = parseMetric(arguments[3])
    let limit = positiveInteger(arguments[4], "limit")
    echo $encodedResults(store.search(parseVector(arguments[5]), metric, limit), metric)
  else:
    usage()

when isMainModule:
  try:
    main()
  except CatchableError as error:
    stderr.writeLine("star-vector: " & error.msg)
    quit(1)
