(in-package #:star-vector)

(include "star_vector.h")

(ctype size-t "size_t")

(constant (+sv-ok+ "SV_OK"))
(constant (+sv-error-invalid-argument+ "SV_ERROR_INVALID_ARGUMENT"))
(constant (+sv-error-invalid-document+ "SV_ERROR_INVALID_DOCUMENT"))
(constant (+sv-error-not-found+ "SV_ERROR_NOT_FOUND"))
(constant (+sv-error-io+ "SV_ERROR_IO"))
(constant (+sv-error-internal+ "SV_ERROR_INTERNAL"))

(constant (+sv-metric-cosine+ "SV_METRIC_COSINE"))
(constant (+sv-metric-dot+ "SV_METRIC_DOT"))
(constant (+sv-metric-euclidean+ "SV_METRIC_EUCLIDEAN"))
(constant (+sv-metric-manhattan+ "SV_METRIC_MANHATTAN"))
