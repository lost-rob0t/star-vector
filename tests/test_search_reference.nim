import std/[algorithm, json, math, random, strutils, unittest]
import star_vector

suite "bounded heap against independent exhaustive reference":
  test "all metrics, zero vectors, ties, limits and detached documents":
    var rng = initRand(42)
    let store = openVectorStore("", 3)
    defer: store.close()
    var vectors: seq[seq[float32]]
    for i in 0 ..< 200:
      let v = @[float32(rng.rand(4) - 2), float32(rng.rand(4) - 2), float32(rng.rand(4) - 2)]
      vectors.add(v)
      store.upsertDocument(%*{"id": "doc:" & align($i, 3, '0'), "dataset": "synthetic",
        "dtype": "document", "schemaVersion": "0.10.1"}, v)
    let query = @[1.0'f32, 0.0'f32, 0.0'f32]
    for metric in VectorMetric:
      var reference: seq[tuple[value: float64, id: string]]
      for i, v in vectors:
        let length = sqrt(float64(v[0]*v[0] + v[1]*v[1] + v[2]*v[2]))
        if metric == vmCosine and length == 0: continue
        let value = case metric
          of vmCosine: float64(v[0]) / length
          of vmDot: float64(v[0])
          of vmEuclidean: sqrt(float64((v[0]-1)*(v[0]-1) + v[1]*v[1] + v[2]*v[2]))
          of vmManhattan: float64(abs(v[0]-1) + abs(v[1]) + abs(v[2]))
        reference.add((value, "doc:" & align($i, 3, '0')))
      reference.sort(proc(a, b: tuple[value: float64, id: string]): int =
        result = if metric in {vmDot, vmCosine}: cmp(b.value, a.value) else: cmp(a.value, b.value)
        if result == 0: result = cmp(a.id, b.id))
      for k in [0, 1, 3, 17, 250]:
        let found = store.search(query, metric, k)
        check found.len == min(k, reference.len)
        for i, row in found:
          check row.id == reference[i].id
          check abs(row.value - reference[i].value) < 1e-12
    let found = store.search(query, vmDot, 1)
    found[0].document["dataset"] = %"mutated"
    check store.getDocument(found[0].id)["dataset"].getStr == "synthetic"
