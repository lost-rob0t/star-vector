(in-package #:star-vector)

(define-condition star-vector-error (error)
  ((status :initarg :status :reader error-status)
   (message :initarg :message :reader error-message))
  (:report (lambda (condition stream)
             (format stream "Star Vector error ~d: ~a"
                     (error-status condition)
                     (error-message condition)))))

(defclass store ()
  ((pointer :initarg :pointer :accessor store-pointer)))

(defvar *star-vector-library* nil)

(defmacro with-star-vector-call (&body body)
  #+sbcl
  `(sb-int:with-float-traps-masked
       (:invalid :divide-by-zero :overflow :underflow :inexact)
     ,@body)
  #-sbcl
  `(progn ,@body))

(cffi:defcfun ("sv_open" %sv-open) :int32
  (path :string)
  (dimensions size-t)
  (out-store :pointer))
(cffi:defcfun ("sv_close" %sv-close) :int32
  (store :pointer))
(cffi:defcfun ("sv_dimensions" %sv-dimensions) size-t
  (store :pointer))
(cffi:defcfun ("sv_len" %sv-len) size-t
  (store :pointer))
(cffi:defcfun ("sv_upsert_document" %sv-upsert-document) :int32
  (store :pointer)
  (document-json :string)
  (vector :pointer)
  (vector-length size-t))
(cffi:defcfun ("sv_get_document" %sv-get-document) :int32
  (store :pointer)
  (id :string)
  (out-document-json :pointer))
(cffi:defcfun ("sv_delete_document" %sv-delete-document) :int32
  (store :pointer)
  (id :string)
  (out-deleted :pointer))
(cffi:defcfun ("sv_search_json" %sv-search-json) :int32
  (store :pointer)
  (query :pointer)
  (query-length size-t)
  (metric :int)
  (limit size-t)
  (out-results-json :pointer))
(cffi:defcfun ("sv_last_error" %sv-last-error) :pointer
  (store :pointer))
(cffi:defcfun ("sv_string_free" %sv-string-free) :void
  (value :pointer))

(defun ensure-library-loaded ()
  (unless *star-vector-library*
    (setf *star-vector-library*
          (cffi:load-foreign-library
           (or (uiop:getenv "STAR_VECTOR_LIBRARY") "libstar_vector.so")))))

(defun pointer-or-null (store)
  (if store (store-pointer store) (cffi:null-pointer)))

(defun last-error-message (store)
  (let ((pointer (%sv-last-error (pointer-or-null store))))
    (if (cffi:null-pointer-p pointer)
        "unknown error"
        (cffi:foreign-string-to-lisp pointer :encoding :utf-8))))

(defun check-status (status store)
  (unless (= status +sv-ok+)
    (error 'star-vector-error
           :status status
           :message (last-error-message store))))

(defun open-store (path dimensions)
  (ensure-library-loaded)
  (cffi:with-foreign-object (out-store :pointer)
    (setf (cffi:mem-ref out-store :pointer) (cffi:null-pointer))
    (let ((status (with-star-vector-call
                    (%sv-open path dimensions out-store))))
      (check-status status nil)
      (make-instance 'store :pointer (cffi:mem-ref out-store :pointer)))))

(defun close-store (store)
  (unless (cffi:null-pointer-p (store-pointer store))
    (check-status (with-star-vector-call
                    (%sv-close (store-pointer store)))
                  store)
    (setf (store-pointer store) (cffi:null-pointer)))
  t)

(defmacro with-store ((name path dimensions) &body body)
  `(let ((,name (open-store ,path ,dimensions)))
     (unwind-protect
          (progn ,@body)
       (close-store ,name))))

(defun store-dimensions (store)
  (%sv-dimensions (store-pointer store)))

(defun store-count (store)
  (%sv-len (store-pointer store)))

(defun call-with-vector (vector function)
  (let* ((values (coerce vector 'vector))
         (length (length values)))
    (cffi:with-foreign-object (foreign-vector :float length)
      (dotimes (index length)
        (setf (cffi:mem-aref foreign-vector :float index)
              (coerce (aref values index) 'single-float)))
      (funcall function foreign-vector length))))

(defun upsert-document (store document-json vector)
  (call-with-vector
   vector
   (lambda (foreign-vector length)
     (check-status
      (with-star-vector-call
        (%sv-upsert-document (store-pointer store) document-json foreign-vector length))
      store)))
  t)

(defun call-for-owned-string (store function)
  (cffi:with-foreign-object (out-string :pointer)
    (setf (cffi:mem-ref out-string :pointer) (cffi:null-pointer))
    (check-status (with-star-vector-call
                    (funcall function out-string))
                  store)
    (let ((pointer (cffi:mem-ref out-string :pointer)))
      (unwind-protect
           (cffi:foreign-string-to-lisp pointer :encoding :utf-8)
        (%sv-string-free pointer)))))

(defun get-document (store id)
  (call-for-owned-string
   store
   (lambda (out-string)
     (%sv-get-document (store-pointer store) id out-string))))

(defun delete-document (store id)
  (cffi:with-foreign-object (deleted :int32)
    (setf (cffi:mem-ref deleted :int32) 0)
    (check-status (with-star-vector-call
                    (%sv-delete-document (store-pointer store) id deleted))
                  store)
    (not (zerop (cffi:mem-ref deleted :int32)))))

(defun metric-value (metric)
  (ecase metric
    (:cosine +sv-metric-cosine+)
    (:dot +sv-metric-dot+)
    (:euclidean +sv-metric-euclidean+)
    (:manhattan +sv-metric-manhattan+)))

(defun search (store query metric limit)
  (call-with-vector
   query
   (lambda (foreign-vector length)
     (call-for-owned-string
      store
      (lambda (out-string)
        (%sv-search-json (store-pointer store)
                         foreign-vector
                         length
                         (metric-value metric)
                         limit
                         out-string))))))
