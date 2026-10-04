(asdf:defsystem "star-vector"
  :description "Common Lisp CFFI client for the Star Vector Nim library"
  :version "0.1.0"
  :license "AGPL-3.0-only"
  :defsystem-depends-on ("cffi-grovel")
  :depends-on ("cffi")
  :serial t
  :components ((:file "package")
               (:cffi-grovel-file "grovel")
               (:file "client"))
  :in-order-to ((test-op (test-op "star-vector/tests"))))

(asdf:defsystem "star-vector/tests"
  :depends-on ("star-vector")
  :serial t
  :components ((:file "tests"))
  :perform (test-op (operation component)
             (declare (ignore operation component))
             (uiop:symbol-call :star-vector/tests :run-tests)))
