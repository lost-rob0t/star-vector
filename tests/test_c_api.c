#define _POSIX_C_SOURCE 200809L

#include "star_vector.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
  char path[] = "/tmp/star-vector-c-XXXXXX";
  int temporary = mkstemp(path);
  assert(temporary >= 0);
  close(temporary);
  unlink(path);

  const char *document =
      "{\"id\":\"doc:c-api\",\"dataset\":\"tests\","
      "\"dtype\":\"document\",\"schemaVersion\":\"0.10.1\"}";
  float vector[] = {1.0f, 0.0f, 0.0f};
  sv_store_t store = NULL;
  assert(strcmp(sv_version(), "0.1.0") == 0);
  assert(sv_open(path, 3, &store) == SV_OK);
  assert(store != NULL);
  assert(sv_dimensions(store) == 3);
  assert(sv_upsert_document(store, document, vector, 3) == SV_OK);
  assert(sv_len(store) == 1);

  char *stored = NULL;
  assert(sv_get_document(store, "doc:c-api", &stored) == SV_OK);
  assert(stored != NULL);
  assert(strstr(stored, "\"id\":\"doc:c-api\"") != NULL);
  sv_string_free(stored);

  char *results = NULL;
  assert(sv_search_json(store, vector, 3, SV_METRIC_COSINE, 10, &results) == SV_OK);
  assert(results != NULL);
  assert(strstr(results, "\"id\":\"doc:c-api\"") != NULL);
  assert(strstr(results, "\"metric\":\"cosine\"") != NULL);
  sv_string_free(results);

  assert(sv_upsert_document(store, document, vector, 2) == SV_ERROR_INVALID_ARGUMENT);
  assert(strlen(sv_last_error(store)) > 0);

  int32_t deleted = 0;
  assert(sv_delete_document(store, "doc:c-api", &deleted) == SV_OK);
  assert(deleted == 1);
  assert(sv_get_document(store, "doc:c-api", &stored) == SV_ERROR_NOT_FOUND);
  assert(stored == NULL);
  assert(sv_close(store) == SV_OK);

  assert(sv_open(path, 3, &store) == SV_OK);
  assert(sv_len(store) == 0);
  assert(sv_close(store) == SV_OK);
  unlink(path);
  return 0;
}
