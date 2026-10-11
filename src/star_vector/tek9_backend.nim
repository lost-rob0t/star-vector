import std/[json, os, osproc, streams]
when defined(posix): import std/posix

type Tek9Backend* = ref object
  process: Process
  failed: bool

const tek9Worker* {.strdefine.} = "star-vector-tek9-worker"

proc stop*(backend: Tek9Backend) =
  if backend.isNil or backend.process.isNil: return
  let process = backend.process
  backend.process = nil
  if process.running: process.terminate()
  if process.waitForExit(3000) == -1:
    process.kill()
    discard process.waitForExit()
  process.close()

proc send*(backend: Tek9Backend, request: JsonNode) =
  if backend.failed or backend.process.isNil:
    raise newException(IOError, "Tek9 worker is unavailable; reopen the store")
  try:
    backend.process.inputStream.writeLine($request)
    backend.process.inputStream.flush()
  except CatchableError:
    backend.failed = true
    backend.stop()
    raise

proc receive*(backend: Tek9Backend): JsonNode =
  var line: string
  try:
    if not backend.process.outputStream.readLine(line):
      raise newException(IOError, "Tek9 worker exited; mutation outcome may be unknown; reopen to inspect")
    result = parseJson(line)
    if not result.hasKey("ok"):
      raise newException(IOError, "Invalid Tek9 worker response")
  except CatchableError:
    backend.failed = true
    backend.stop()
    raise
  if not result["ok"].getBool:
    raise newException(IOError, "Tek9: " & result["error"].getStr)

proc request*(backend: Tek9Backend, value: JsonNode): JsonNode =
  backend.send(value)
  backend.receive()

proc openTek9*(path: string, dimensions: int, source = "", total = 0): Tek9Backend =
  new(result)
  let worker = getEnv("STAR_VECTOR_TEK9_WORKER", tek9Worker)
  result.process = startProcess(worker, options = {poUsePath})
  try:
    when defined(posix):
      # Other handles' children must not inherit this worker's pipe endpoints.
      for fd in [result.process.inputHandle, result.process.outputHandle,
                 result.process.errorHandle]:
        if fcntl(fd, F_SETFD, FD_CLOEXEC) == -1: raiseOSError(osLastError())
    var message = %*{"op": "open", "path": absolutePath(path), "dimensions": dimensions}
    if source.len > 0:
      message["source"] = %source
      message["total"] = %total
    discard result.request(message)
  except CatchableError:
    result.stop()
    raise

proc close*(backend: Tek9Backend) =
  if backend.isNil or backend.process.isNil: return
  try:
    discard backend.request(%*{"op": "close"})
  finally:
    backend.stop()
