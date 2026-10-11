(require :asdf)
(asdf:load-asd (truename "lisp/star-vector.asd"))
(asdf:load-system "star-vector")
(let* ((path (uiop:getenv "STAR_VECTOR_TEXT_TEST_STORE"))
       (profile (star-vector:text-embedding-profile))
       (document nil))
  (star-vector:with-store (store path 300)
    (setf document (star-vector:get-document store "synthetic-shared"))
    (assert (cl:search "javascript" document))
    (assert (cl:search "en_core_web_md" (star-vector:embedding-profile store)))
    (star-vector:bind-embedding-profile store profile))
  ;; Synthetic canonical fixture; preserve the original identity/provenance extension.
  (star-vector:upsert-text-document path
    "{\"id\":\"synthetic-shared\",\"dataset\":\"synthetic-real-model-proof\",\"dtype\":\"document\",\"schemaVersion\":\"0.10.1\",\"rev\":\"lisp\",\"rawContent\":\"A gardener grows tomatoes and fresh vegetables.\",\"provenance\":{\"fixture\":true},\"extensions\":{\"unrelated\":{\"keep\":1}}}")
  (dolist (metric '(:cosine :dot :euclidean :manhattan))
    (assert (cl:search "synthetic-shared"
      (star-vector:search-text path "A gardener grows tomatoes and fresh vegetables." :metric metric :limit 1))))
  (format t "Common Lisp text client passed~%"))
