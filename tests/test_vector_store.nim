import std/[json, math, os, sequtils, unittest]

import star_vector

proc document(id: string): JsonNode =
  %*{
    "id": id,
    "dataset": "star-vector-tests",
    "dtype": "document",
    "schemaVersion": "0.10.1"
  }

suite "vector store":
  let path = getTempDir() / ("star-vector-" & $getCurrentProcessId() & ".json")

  setup:
    if fileExists(path):
      removeFile(path)

  teardown:
    if fileExists(path):
      removeFile(path)

  test "persists valid StarIntel documents and rejects invalid input":
    let store = openVectorStore(path, 3)
    store.upsertDocument(document("doc:alpha"), @[1.0'f32, 0.0'f32, 0.0'f32])
    check store.len == 1
    check store.getDocument("doc:alpha") == document("doc:alpha")

    expect VectorStoreError:
      store.upsertDocument(
        %*{
          "id": "doc:invalid",
          "dataset": "star-vector-tests",
          "dtype": "document",
          "schemaVersion": "0.9.0"
        },
        @[1.0'f32, 0.0'f32, 0.0'f32]
      )
    expect VectorStoreError:
      store.upsertDocument(document("doc:wrong-dimension"), @[1.0'f32])
    check store.len == 1

    store.close()
    let reopened = openVectorStore(path, 3)
    check reopened.len == 1
    check reopened.getDocument("doc:alpha") == document("doc:alpha")
    expect VectorStoreError:
      discard openVectorStore(path, 2)
    reopened.close()

  test "validates canonical specialized document fields":
    let email = %*{
      "id": "email:test",
      "dataset": "star-vector-tests",
      "dtype": "email",
      "schemaVersion": "0.10.1",
      "address": "alice@example.test"
    }
    check validateStarIntelDocument(email).ok

    email["address"] = %"not-an-email"
    let invalidEmail = validateStarIntelDocument(email)
    check not invalidEmail.ok
    check invalidEmail.category == "pattern_mismatch"

    let unknownType = validateStarIntelDocument(%*{
      "id": "doc:unknown",
      "dataset": "star-vector-tests",
      "dtype": "invented-type",
      "schemaVersion": "0.10.1"
    })
    check not unknownType.ok
    check unknownType.category == "unknown_document_type"

  test "searches deterministically with four exact metrics":
    let store = openVectorStore(path, 3)
    store.upsertDocument(document("doc:alpha"), @[1.0'f32, 0.0'f32, 0.0'f32])
    store.upsertDocument(document("doc:beta"), @[0.0'f32, 1.0'f32, 0.0'f32])
    store.upsertDocument(document("doc:gamma"), @[0.5'f32, 0.5'f32, 0.0'f32])
    store.upsertDocument(document("doc:zero"), @[0.0'f32, 0.0'f32, 0.0'f32])

    let cosine = store.search(@[1.0'f32, 0.0'f32, 0.0'f32], vmCosine, 10)
    check cosine.len == 3
    check cosine[0].id == "doc:alpha"
    check abs(cosine[0].value - 1.0) < 0.00001
    check cosine[1].id == "doc:gamma"

    let dot = store.search(@[1.0'f32, 0.0'f32, 0.0'f32], vmDot, 2)
    check dot.mapIt(it.id) == @["doc:alpha", "doc:gamma"]
    check abs(dot[1].value - 0.5) < 0.00001

    let euclidean = store.search(@[1.0'f32, 0.0'f32, 0.0'f32], vmEuclidean, 4)
    check euclidean.mapIt(it.id) ==
      @["doc:alpha", "doc:gamma", "doc:zero", "doc:beta"]
    check abs(euclidean[1].value - sqrt(0.5)) < 0.00001

    let manhattan = store.search(@[1.0'f32, 0.0'f32, 0.0'f32], vmManhattan, 4)
    check manhattan.mapIt(it.id) ==
      @["doc:alpha", "doc:gamma", "doc:zero", "doc:beta"]

    expect VectorStoreError:
      discard store.search(@[0.0'f32, 0.0'f32, 0.0'f32], vmCosine, 1)

    check store.deleteDocument("doc:beta")
    check not store.deleteDocument("doc:missing")
    expect VectorStoreError:
      discard store.getDocument("doc:beta")
    store.close()

  test "rejects non-finite vector values":
    let store = openVectorStore(path, 2)
    expect VectorStoreError:
      store.upsertDocument(document("doc:nan"), @[NaN.float32, 0.0'f32])
    expect VectorStoreError:
      discard store.search(@[Inf.float32, 0.0'f32], vmDot, 1)
    store.close()
