#ifndef STAR_VECTOR_H
#define STAR_VECTOR_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define SV_API __declspec(dllimport)
#else
#define SV_API
#endif

typedef void *sv_store_t;

/* Independent handles may be used concurrently. Calls on one handle, including
 * close, must be serialized by the caller. */

typedef int32_t sv_status_t;
#define SV_OK 0
#define SV_ERROR_INVALID_ARGUMENT 1
#define SV_ERROR_INVALID_DOCUMENT 2
#define SV_ERROR_NOT_FOUND 3
#define SV_ERROR_IO 4
#define SV_ERROR_INTERNAL 5

typedef int32_t sv_metric_t;
#define SV_METRIC_COSINE 0
#define SV_METRIC_DOT 1
#define SV_METRIC_EUCLIDEAN 2
#define SV_METRIC_MANHATTAN 3

SV_API const char *sv_version(void);
SV_API int32_t sv_open(const char *path, size_t dimensions, sv_store_t *out_store);
SV_API int32_t sv_close(sv_store_t store);
SV_API int32_t sv_flush(sv_store_t store);
SV_API size_t sv_dimensions(sv_store_t store);
SV_API size_t sv_len(sv_store_t store);
SV_API int32_t sv_upsert_document(sv_store_t store, const char *document_json,
                                  const float *vector, size_t vector_len);
/* Binding is immutable and allowed only on empty, non-legacy stores. Matching
 * repeated binds are idempotent. Profile-aware writes are checked atomically;
 * ordinary raw upserts are rejected on profile-bound stores. JSON object keys
 * are canonicalized before equality checks. get returns JSON null if unbound.
 * Returned strings use sv_string_free, as with document/search results. */
SV_API int32_t sv_bind_embedding_profile(sv_store_t store, const char *profile_json);
SV_API int32_t sv_get_embedding_profile(sv_store_t store, char **out_profile_json);
SV_API int32_t sv_upsert_document_with_profile(sv_store_t store,
  const char *document_json, const float *vector, size_t vector_len,
  const char *profile_json);
SV_API int32_t sv_get_document(sv_store_t store, const char *id,
                               char **out_document_json);
SV_API int32_t sv_delete_document(sv_store_t store, const char *id,
                                  int32_t *out_deleted);
SV_API int32_t sv_search_json(sv_store_t store, const float *query,
                              size_t query_len, sv_metric_t metric,
                              size_t limit, char **out_results_json);
SV_API const char *sv_last_error(sv_store_t store);
SV_API void sv_string_free(char *value);

#ifdef __cplusplus
}
#endif

#endif
