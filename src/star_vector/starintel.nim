import std/[json, math, strutils, tables]

const
  StarIntelReleaseVersion* = "0.10.1"
  schemaText = staticRead("../../schema/starintel-schema.json")
  manifestText = staticRead("../../schema/starintel-portable-manifest.json")

type
  DocumentValidation* = object
    ok*: bool
    category*: string
    message*: string
    id*: string

let
  canonicalSchema = parseJson(schemaText)
  portableManifest = parseJson(manifestText)

proc success(id: string): DocumentValidation =
  DocumentValidation(ok: true, id: id)

proc failure(category, message: string): DocumentValidation =
  DocumentValidation(ok: false, category: category, message: message)

proc normalizedName(value: string): string =
  for character in value.toLowerAscii:
    if character.isAlphaNumeric:
      result.add(character)

proc documentDefinitions(): Table[string, string] =
  var schemaNames = initTable[string, string]()
  for name, _ in canonicalSchema["$defs"].pairs:
    schemaNames[normalizedName(name)] = name

  for definition in portableManifest["types"].items:
    if definition.kind == JObject and definition.hasKey("kind") and
       definition["kind"].getStr == "document":
      let qualified = definition["name"].getStr
      let dtype = qualified.split('/')[^1]
      let normalized = normalizedName(dtype)
      if schemaNames.hasKey(normalized):
        result[dtype] = schemaNames[normalized]

let canonicalDocumentDefinitions = documentDefinitions()

proc jsonType(value: JsonNode): string =
  case value.kind
  of JNull: "null"
  of JBool: "boolean"
  of JInt: "integer"
  of JFloat: "number"
  of JString: "string"
  of JArray: "array"
  of JObject: "object"

proc matchesType(value: JsonNode, expected: string): bool =
  case expected
  of "null": value.kind == JNull
  of "boolean": value.kind == JBool
  of "integer": value.kind == JInt
  of "number": value.kind in {JInt, JFloat}
  of "string": value.kind == JString
  of "array": value.kind == JArray
  of "object": value.kind == JObject
  else: false

proc numericValue*(value: JsonNode): float64 =
  if value.kind == JInt:
    value.getInt.float64
  else:
    value.getFloat

proc containsOnly(value: string, allowed: set[char]): bool =
  for character in value:
    if character notin allowed:
      return false
  true

proc containsNoAsciiWhitespace(value: string): bool =
  for character in value:
    if character.isSpaceAscii:
      return false
  true

proc matchesCanonicalPattern(value, pattern: string): bool =
  case pattern
  of "^[A-Za-z0-9._~:/+-]{1,512}$":
    value.len in 1 .. 512 and value.containsOnly(
      {'A' .. 'Z', 'a' .. 'z', '0' .. '9', '.', '_', '~', ':', '/', '+', '-'}
    )
  of "^[^[:space:]@]+@[^[:space:]@]+$":
    let separator = value.find('@')
    separator > 0 and separator < value.high and
      value.find('@', separator + 1) < 0 and
      value.containsNoAsciiWhitespace
  of "^\\+?[0-9(). -]{3,32}$":
    let digits = if value.startsWith('+'): value[1 .. ^1] else: value
    digits.len in 3 .. 32 and digits.containsOnly({'0' .. '9', '(', ')', '.', ' ', '-'})
  of "^[+-]?[0-9]+(?:\\.[0-9]+)?$":
    var offset = 0
    if value.len > 0 and value[0] in {'+', '-'}:
      offset = 1
    if offset >= value.len:
      return false
    let decimalPoint = value.find('.', offset)
    if decimalPoint < 0:
      return value[offset .. ^1].containsOnly({'0' .. '9'})
    decimalPoint > offset and decimalPoint < value.high and
      value.find('.', decimalPoint + 1) < 0 and
      value[offset ..< decimalPoint].containsOnly({'0' .. '9'}) and
      value[decimalPoint + 1 .. ^1].containsOnly({'0' .. '9'})
  else:
    raise newException(ValueError, "unsupported canonical pattern: " & pattern)

proc resolveReference(reference: string): JsonNode =
  const prefix = "#/$defs/"
  if not reference.startsWith(prefix):
    raise newException(ValueError, "unsupported schema reference: " & reference)
  let name = reference[prefix.len .. ^1]
  if not canonicalSchema["$defs"].hasKey(name):
    raise newException(ValueError, "unknown schema reference: " & reference)
  canonicalSchema["$defs"][name]

proc validateValue(value, schema: JsonNode, path: string): DocumentValidation

proc validateObject(value, schema: JsonNode, path: string): DocumentValidation =
  let properties =
    if schema.hasKey("properties"): schema["properties"]
    else: newJObject()

  if schema.hasKey("required"):
    for required in schema["required"].items:
      let name = required.getStr
      if not value.hasKey(name):
        return failure("missing_required_field", path & ": missing required field " & name)

  var allowAdditional = true
  var additionalSchema: JsonNode
  if schema.hasKey("additionalProperties"):
    let additional = schema["additionalProperties"]
    case additional.kind
    of JBool:
      allowAdditional = additional.getBool
    of JObject:
      additionalSchema = additional
    else:
      discard

  for name, child in value.pairs:
    if properties.hasKey(name):
      let checked = validateValue(child, properties[name], path & "." & name)
      if not checked.ok:
        return checked
    elif not additionalSchema.isNil:
      let checked = validateValue(child, additionalSchema, path & "." & name)
      if not checked.ok:
        return checked
    elif not allowAdditional:
      return failure("undeclared_field", path & ": undeclared field " & name)
  success("")

proc validateValue(value, schema: JsonNode, path: string): DocumentValidation =
  if schema.kind != JObject or schema.len == 0:
    return success("")

  if schema.hasKey("$ref"):
    return validateValue(value, resolveReference(schema["$ref"].getStr), path)

  if schema.hasKey("allOf"):
    for branch in schema["allOf"].items:
      let checked = validateValue(value, branch, path)
      if not checked.ok:
        return checked

  if schema.hasKey("const") and value != schema["const"]:
    return failure("invalid_constant", path & ": unexpected constant")

  if schema.hasKey("enum"):
    var found = false
    for allowed in schema["enum"].items:
      if value == allowed:
        found = true
        break
    if not found:
      return failure("invalid_enum", path & ": value is not in enum")

  if schema.hasKey("type"):
    let expected = schema["type"].getStr
    if not matchesType(value, expected):
      return failure("wrong_type", path & ": expected " & expected & ", got " & jsonType(value))

  if value.kind == JString and schema.hasKey("pattern"):
    try:
      if not matchesCanonicalPattern(value.getStr, schema["pattern"].getStr):
        return failure("pattern_mismatch", path & ": string does not match pattern")
    except ValueError as error:
      return failure("schema_error", path & ": " & error.msg)

  if value.kind in {JInt, JFloat}:
    let number = numericValue(value)
    if schema.hasKey("minimum") and number < numericValue(schema["minimum"]):
      return failure("below_minimum", path & ": number is below minimum")
    if schema.hasKey("maximum") and number > numericValue(schema["maximum"]):
      return failure("above_maximum", path & ": number is above maximum")

  if value.kind == JArray and schema.hasKey("items"):
    for index in 0 ..< value.len:
      let checked = validateValue(value[index], schema["items"], path & "[" & $index & "]")
      if not checked.ok:
        return checked

  if value.kind == JObject:
    let checked = validateObject(value, schema, path)
    if not checked.ok:
      return checked

  success("")

proc validateStarIntelDocument*(document: JsonNode): DocumentValidation =
  if document.kind != JObject:
    return failure("wrong_type", "$: expected object")
  for field in ["id", "dataset", "dtype", "schemaVersion"]:
    if not document.hasKey(field):
      return failure("missing_required_field", "$: missing required field " & field)
    if document[field].kind != JString:
      return failure("wrong_type", "$." & field & ": expected string")

  if document["schemaVersion"].getStr != StarIntelReleaseVersion:
    return failure(
      "unsupported_spec_version",
      "$.schemaVersion: expected " & StarIntelReleaseVersion
    )

  let dtype = document["dtype"].getStr
  if not canonicalDocumentDefinitions.hasKey(dtype):
    return failure("unknown_document_type", "$.dtype: unknown canonical document type " & dtype)

  let definition = canonicalSchema["$defs"][canonicalDocumentDefinitions[dtype]]
  result = validateValue(document, definition, "$")
  if result.ok:
    result.id = document["id"].getStr

proc parseStarIntelDocument*(encoded: string): tuple[document: JsonNode, validation: DocumentValidation] =
  try:
    result.document = parseJson(encoded)
    result.validation = validateStarIntelDocument(result.document)
  except JsonParsingError as error:
    result.document = newJNull()
    result.validation = failure("invalid_json", error.msg)
