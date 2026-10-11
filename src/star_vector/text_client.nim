## Local convenience client. Each call starts the pinned Python helper; reuse
## the Python DocumentStore for high-throughput ingestion. No shell or daemon.
import std/[json, os, osproc, streams]

const textHelper* {.strdefine.} = "star-vector-text"

proc textHelperPath*(): string = getEnv("STAR_VECTOR_TEXT_HELPER", textHelper)

proc textRequest(arguments: seq[string], input: string): JsonNode =
  let child = startProcess(textHelperPath(), args = arguments,
                          options = {poUsePath, poStdErrToStdOut})
  defer: child.close()
  child.inputStream.write(input)
  child.inputStream.close()
  let response = child.outputStream.readAll()
  if child.waitForExit() != 0:
    raise newException(IOError, response)
  parseJson(response)

proc textEmbeddingProfile*(): JsonNode =
  textRequest(@["embedding-profile"], "")

proc upsertTextDocument*(path: string, document: JsonNode): JsonNode =
  textRequest(@["embed-upsert", path, "-"], $document)

proc searchText*(path, query: string, metric = "cosine", limit = 10): JsonNode =
  if limit < 0: raise newException(ValueError, "search limit cannot be negative")
  textRequest(@["embed-search", path, metric, $limit, "-"], query)
