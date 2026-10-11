(asdf:defsystem "star-vector-tek9"
  :description "Private Star Vector adapter importing the Tek9 producer library"
  :license "AGPL-3.0-only"
  :depends-on ("tek9" "jsown")
  :components ((:file "tek9-worker")))
