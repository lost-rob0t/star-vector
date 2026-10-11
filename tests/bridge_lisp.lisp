(require :asdf)
(asdf:load-asd (truename (merge-pathnames "../lisp/star-vector.asd" *load-truename*)))
(asdf:load-system "star-vector")
(let ((path (uiop:getenv "SV_TEST_STORE")))
  (star-vector:with-store (store path 3)
    (assert (search "doc:a" (star-vector:get-document store "doc:a")))
    (star-vector:upsert-document
     store "{\"id\":\"doc:lisp-bridge\",\"dataset\":\"synthetic\",\"dtype\":\"document\",\"schemaVersion\":\"0.10.1\"}"
     #(1.0 0.0 0.0))))
#+sbcl (sb-ext:gc :full t) ; The C library must preserve SBCL's signal handlers.
;; Verify that bridge startup failure reaches callers as a CFFI condition.
(let ((worker (uiop:getenv "STAR_VECTOR_TEK9_WORKER")) (failed nil))
  (unwind-protect
       (progn
         (setf (uiop:getenv "STAR_VECTOR_TEK9_WORKER") "/bin/false")
         (handler-case (star-vector:open-store (uiop:getenv "SV_TEST_STORE") 3)
           (star-vector:star-vector-error (e)
             (assert (= 4 (star-vector:error-status e)))
             (setf failed t)))
         (assert failed))
    (setf (uiop:getenv "STAR_VECTOR_TEK9_WORKER") worker)))
