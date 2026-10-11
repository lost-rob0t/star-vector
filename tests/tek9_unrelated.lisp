;; Synthetic foreign producer store and independent, read-only inventory probe.
(require :asdf)
(let ((*standard-output* *error-output*)) (asdf:load-system :tek9))

(defun hex-octets (octets)
  (format nil "~{~2,'0X~}" (coerce octets 'list)))

(let ((path (uiop:ensure-directory-pathname (uiop:getenv "SV_TEST_STORE"))))
  (when (equal (uiop:getenv "SV_TEST_ACTION") "create")
    (let ((db (tek9:open-database
               (tek9:new-database "unrelated-synthetic" :path path
                                  :durability :full :index-definitions nil))))
      (unwind-protect
           (progn
             (tek9:put-bulk db (list (tek9:new-document :id "foreign:one" :value "keep me")))
             (tek9:put-bulk db (list (tek9:new-document :id "foreign:two" :value '(1 2 3)))
                              :database-name "foreign/custom"))
        (tek9:close-database db))))
  (lmdb:with-env (env path :read-only t :if-does-not-exist :error :max-dbs 64)
    (let ((catalog (lmdb:get-db nil :env env :if-does-not-exist :error
                                   :key-encoding :octets :value-encoding :octets))
          (names nil) (inventory nil))
      (lmdb:with-txn (:env env :write nil)
        (lmdb:do-db (name value catalog)
          ;; Native LMDB catalog names lack the trailing NUL expected by the
          ;; wrapper's :UTF-8 encoding. The fixture's DB names are ASCII.
          (push (map 'string #'code-char name) names)))
      (dolist (name (sort names #'string<))
        (let ((db (lmdb:get-db name :env env :if-does-not-exist :error
                                   :key-encoding :octets :value-encoding :octets))
              (records nil))
          (lmdb:with-txn (:env env :write nil)
            (lmdb:do-db (key value db)
              (push (list (hex-octets key) (hex-octets value)) records)))
          (push (jsown:new-js ("name" name) ("records" (nreverse records))) inventory)))
      (write-line (jsown:to-json (jsown:new-js ("databases" (nreverse inventory))))))))
