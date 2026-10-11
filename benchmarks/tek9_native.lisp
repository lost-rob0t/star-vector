;; Loaded by storage_benchmark.py with the exact same Tek9 library as the worker.
(let* ((db (tek9:open-database
            (tek9:new-database "synthetic-benchmark"
                               :path (uiop:ensure-directory-pathname (uiop:getenv "SV_BENCH_STORE"))
                               :durability :full :index-definitions nil)))
       (start (get-internal-real-time)) (count 0))
  (unwind-protect
       (progn
         (tek9:map-database db :map-fn (lambda (id doc) (declare (ignore id doc)) (incf count)))
         (write-line (jsown:to-json
                      (jsown:new-js ("records" count)
                                    ("native_scan_seconds" (/ (- (get-internal-real-time) start)
                                                               (float internal-time-units-per-second 1d0)))))))
    (tek9:close-database db)))
