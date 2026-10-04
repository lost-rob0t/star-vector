version = "0.1.0"
author = "StarIntel"
description = "Exact vector storage and search for StarIntel documents"
license = "AGPL-3.0-only"
srcDir = "src"
bin = @["star_vector_cli"]

requires "nim >= 2.2.0"

task test, "Run Nim tests":
  exec "nim c -r --threads:on --mm:orc --path:src tests/test_vector_store.nim"
