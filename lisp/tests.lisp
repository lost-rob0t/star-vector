(in-package #:star-vector/tests)

(defun ensure (condition format-control &rest arguments)
  (unless condition
    (error (apply #'format nil format-control arguments))))

(defun temporary-store-path ()
  (format nil "/tmp/star-vector-lisp-~d-~d.json"
          (get-universal-time)
          (random 1000000)))

(defun run-tests ()
  (let ((path (temporary-store-path))
        (document "{\"id\":\"doc:lisp\",\"dataset\":\"tests\",\"dtype\":\"document\",\"schemaVersion\":\"0.10.1\"}"))
    (unwind-protect
         (with-store (store path 3)
           (ensure (= 3 (store-dimensions store)) "Wrong store dimension")
           (upsert-document store document #(1.0 0.0 0.0))
           (ensure (= 1 (store-count store)) "Wrong store count")
           (ensure (cl:search "\"id\":\"doc:lisp\"" (get-document store "doc:lisp"))
                   "Stored document was not returned")
           (ensure (cl:search "\"metric\":\"cosine\""
                              (star-vector:search store #(1.0 0.0 0.0) :cosine 5))
                   "Cosine result was not returned")
           (ensure (delete-document store "doc:lisp") "Document was not deleted")
           (ensure (= 0 (store-count store)) "Store was not empty after deletion"))
      (when (probe-file path)
        (delete-file path))))
  t)
