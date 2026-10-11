;; A test-only producer transaction. No crash hooks exist in the application.
(require :asdf)
(let ((*standard-output* *error-output*)) (asdf:load-system :tek9))
(let ((db (tek9:open-database
           (tek9:new-database "synthetic-crash-test"
                              :path (uiop:ensure-directory-pathname (uiop:getenv "SV_TEST_STORE"))
                              :durability :full :index-definitions nil))))
  (unwind-protect
       (tek9:with-write-transaction (db :database-names '("star-vector/meta"))
         (tek9:put-bulk db (list (tek9:new-document :id "uncommitted" :value "uncommitted")))
         (let ((meta (tek9:fetch* db "header" :database-name "star-vector/meta")))
           (incf (getf meta :count))
           (incf (getf meta :next))
           (tek9:put-bulk db (list (tek9:new-document :id "header" :value meta))
                          :database-name "star-vector/meta"))
         (write-line "transaction-open") (finish-output)
         (loop (sleep 1)))
    (tek9:close-database db)))
