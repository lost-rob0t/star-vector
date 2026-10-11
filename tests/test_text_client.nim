import std/[json, os]
import star_vector

let path = paramStr(1)
let profile = textEmbeddingProfile()
doAssert profile["dimensions"].getInt == 300
let store = openVectorStore(path, 300)
doAssert store.embeddingProfile() == profile
var document = store.getDocument("synthetic-shared")
doAssert document["rev"].getStr == "python"
store.close()
document["rev"] = %"nim"
document["rawContent"] = %"Ocean waves wash over a sandy beach."
doAssert upsertTextDocument(path, document)["upserted"].getBool
for metric in ["cosine", "dot", "euclidean", "manhattan"]:
  doAssert searchText(path, document["rawContent"].getStr, metric, 1)[0]["id"].getStr == "synthetic-shared"
echo "Nim text client passed"
