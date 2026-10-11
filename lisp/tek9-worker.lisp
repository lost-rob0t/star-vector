(defpackage :star-vector-tek9
  (:use :cl)
  (:export :run))
(in-package :star-vector-tek9)

;; Application adapter only: transactions, encoding and cursors belong to Tek9.
(defparameter +meta+ "star-vector/meta")
(defun field (request key) (jsown:val request key))
(defun reply (&rest pairs)
  (write-line (jsown:to-json (cons :obj pairs)))
  (finish-output))
(defun ok (&rest pairs) (apply #'reply (cons "ok" t) pairs))
(defun scan-row (id record)
  ;; Keep the existing JSON-line protocol, but avoid both full-record temporary
  ;; strings created by generic string escaping and object serialization.
  (check-type record string)
  (write-string "{\"ok\":true,\"id\":")
  (star-vector-wire:write-json-string id *standard-output*)
  (write-string ",\"record\":")
  (star-vector-wire:write-json-string record *standard-output*)
  (write-line "}")
  (finish-output))
(defun header (db) (tek9:fetch* db "header" :database-name +meta+))
(defun save-header (db value)
  (tek9:put-bulk db (list (tek9:new-document :id "header" :value value))
                 :database-name +meta+))
(defun record-id (encoded)
  (field (field (jsown:parse encoded) "document") "id"))
(defun put-record (db request)
  ;; Detect even a long-ID hash collision; never overwrite another document.
  (let ((previous (tek9:fetch* db (field request "id"))))
    (when (and previous
               (not (equal (record-id previous) (record-id (field request "record")))))
      (error "Storage key collision; existing document retained.")))
  (tek9:put-bulk db (list (tek9:new-document :id (field request "id")
                                          :value (field request "record")))))
(defun assert-ready (db)
  (unless (equal (getf (header db) :state) "ready")
    (error "Migration incomplete; resume the explicit migration first.")))

(defun existing-header (path)
  ;; Tek9 OPEN-DATABASE and transaction preparation create DBIs independently
  ;; of our application transaction. Establish ownership before either runs.
  ;; Use the producer's LMDB dependency in read-only/non-creating mode, and its
  ;; public decoder. Normal LMDB reader locking protects the probe from writers.
  (handler-case
      (lmdb:with-env (env path :read-only t :if-does-not-exist :error :max-dbs 64)
        (let ((meta (lmdb:get-db +meta+ :env env :if-does-not-exist :error
                                      :key-encoding :utf-8 :value-encoding :octets)))
          (lmdb:get-db "std" :env env :if-does-not-exist :error)
          (lmdb:with-txn (:env env :write nil)
            (let ((encoded (lmdb:g3t meta "header")))
              (when encoded (tek9:doc-value (tek9:$ encoded)))))))
    (lmdb:lmdb-not-found-error () nil)))

(defun validate-header (meta request)
  (unless (equal (getf meta :format) "star-vector/tek9/1")
    (error "Existing environment has no supported Star Vector header."))
  (unless (= (getf meta :dimensions) (field request "dimensions"))
    (error "vector store dimension mismatch"))
  (let ((source (jsown:val-safe request "source")))
    (if source
        (unless (and (equal source (getf meta :source))
                     (eql (field request "total") (getf meta :total)))
          (error "Migration source fingerprint/count mismatch or destination already in use."))
        (unless (equal (getf meta :state) "ready")
          (error "Migration incomplete; resume the explicit migration first.")))))

(defun open-store-locked (request path)
  (let* ((existing (probe-file (merge-pathnames "data.mdb" path)))
         (source (jsown:val-safe request "source"))
         ;; This binding must finish before NEW-DATABASE/OPEN-DATABASE below.
         (checked (when existing (validate-header (existing-header path) request)))
         (db (tek9:open-database
              (tek9:new-database "star-vector" :path path :durability :full
                                 :index-definitions nil))))
    (declare (ignore checked))
    (handler-case
        (progn
          (tek9:with-write-transaction (db :database-names (list +meta+))
            (let ((meta (header db)))
              (unless meta
                (when existing (error "Existing Tek9 environment has no Star Vector header."))
                (setf meta (list :format "star-vector/tek9/1"
                                 :dimensions (field request "dimensions")
                                 :state (if source "migrating" "ready")
                                 :source source :total (jsown:val-safe request "total")
                                 :next 0 :count 0))
                (save-header db meta))
              (validate-header meta request)))
          db)
      (error (condition) (tek9:close-database db) (error condition)))))

(defun open-store (request)
  ;; Lock the directory inode itself: no lock/ownership file is created in an
  ;; existing environment. Competing first opens wait until the header commits.
  ;; The OS releases this advisory lock even if the worker is killed.
  (let ((path (uiop:ensure-directory-pathname (field request "path"))))
    (uiop:ensure-all-directories-exist (list path))
    (let ((fd (sb-posix:open (namestring path)
                            (logior sb-posix:o-rdonly sb-posix:o-directory))))
      (unwind-protect
           (progn
             (sb-posix:fcntl fd sb-posix:f-setfd 1) ; Linux FD_CLOEXEC
             ;; Linux LOCK_EX; both supported flake targets are Linux.
             (unless (zerop (cffi:foreign-funcall "flock" :int fd :int 2 :int))
               (error "Cannot lock store directory for ownership validation."))
             (open-store-locked request path))
        (sb-posix:close fd)))))

(defun dispatch (db request)
  (let ((op (field request "op")))
    (cond
      ((equal op "status")
       (let ((meta (header db)))
         (ok (cons "count" (getf meta :count))
             (cons "next" (getf meta :next))
             (cons "complete" (if (equal (getf meta :state) "ready") t :false)))))
      ((equal op "get")
       (let ((value (tek9:fetch* db (field request "id"))))
         (ok (cons "record" (or value :null)))))
      ((equal op "scan")
       ;; One snapshot, one decoded record at a time, no collected result list.
       (tek9:map-database db :map-fn
         (lambda (id document)
           (scan-row id (tek9:doc-value document))))
       (ok (cons "end" t)))
      ((equal op "upsert")
       (tek9:with-write-transaction (db :database-names (list +meta+))
         (assert-ready db)
         (let ((meta (header db)))
           (unless (tek9:fetch db (field request "id")) (incf (getf meta :count)))
           (put-record db request)
           (save-header db meta)))
       (ok))
      ((equal op "delete")
       (let ((deleted nil))
         (tek9:with-write-transaction (db :database-names (list +meta+))
           (assert-ready db)
           (let ((previous (tek9:fetch* db (field request "id"))))
             (when (and previous (equal (record-id previous) (field request "documentId")))
               (setf deleted (tek9:delete-document db (field request "id")))))
           (when deleted
             (let ((meta (header db)))
               (decf (getf meta :count)) (save-header db meta))))
         (ok (cons "deleted" (if deleted t :false)))))
      ((equal op "migrate-batch")
       (let ((records (field request "records")))
         (unless (and (listp records) (<= 1 (length records) 128))
           (error "Migration batch must contain 1..128 records."))
         (tek9:with-write-transaction (db :database-names (list +meta+))
           (let ((meta (header db)))
             (unless (and (equal (getf meta :state) "migrating")
                          (= (getf meta :next) (field request "expected"))
                          (<= (+ (getf meta :next) (length records)) (getf meta :total)))
               (error "Migration checkpoint conflict."))
             (dolist (record records)
               (when (tek9:fetch db (field record "id"))
                 (error "Duplicate migration record."))
               (put-record db record))
             (incf (getf meta :next) (length records))
             (incf (getf meta :count) (length records))
             (save-header db meta))))
       (ok))
      ((equal op "publish")
       (tek9:with-write-transaction (db :database-names (list +meta+))
         (let ((meta (header db)))
           (unless (and (= (getf meta :next) (getf meta :total))
                        (= (getf meta :count) (getf meta :total)))
             (error "Migration is incomplete."))
           (setf (getf meta :state) "ready") (save-header db meta)))
       (ok))
      ((equal op "flush") (ok)) ; Every acknowledged write already commits :full.
      (t (error "Unknown storage operation: ~A" op)))))

(defun run ()
  "Serve one private pipe connection. EOF closes the environment. Never evaluate input."
  (let ((db nil))
    (unwind-protect
         (loop for line = (read-line *standard-input* nil) while line do
           (handler-case
               (let* ((request (jsown:parse line)) (op (field request "op")))
                 (cond
                   ((equal op "open")
                    (when db (error "Store already open."))
                    (setf db (open-store request)) (ok))
                   ((equal op "close")
                    (when db (tek9:close-database db) (setf db nil))
                    (ok) (return))
                   (db (dispatch db request))
                   (t (error "Store not open."))))
             (error (condition)
               (reply (cons "ok" :false) (cons "error" (princ-to-string condition))))))
      (when db (tek9:close-database db)))))
