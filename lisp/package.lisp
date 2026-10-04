(defpackage #:star-vector
  (:use #:cl)
  (:shadow #:search)
  (:export
   #:star-vector-error
   #:open-store
   #:close-store
   #:with-store
   #:store-dimensions
   #:store-count
   #:upsert-document
   #:get-document
   #:delete-document
   #:search))

(defpackage #:star-vector/tests
  (:use #:cl)
  (:import-from #:star-vector
                #:with-store
                #:store-dimensions
                #:store-count
                #:upsert-document
                #:get-document
                #:delete-document)
  (:export #:run-tests))
