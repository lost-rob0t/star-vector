;; Application wire encoding only. Storage and cursor decoding belong to Tek9.
(defpackage :star-vector-wire
  (:use :cl)
  (:export :write-json-string))
(in-package :star-vector-wire)

(defun write-json-string (value stream)
  "Write an ASCII JSON string without constructing an escaped copy of VALUE."
  (check-type value string)
  (labels ((unicode-escape (code)
             (write-string "\\u" stream)
             (loop for shift from 12 downto 0 by 4 do
               (write-char (char "0123456789ABCDEF" (ldb (byte 4 shift) code)) stream))))
    (write-char #\" stream)
    (loop with start = 0
          for index below (length value)
          for code = (char-code (char value index))
          when (or (< code 32) (> code 126) (= code 34) (= code 92)) do
            (write-string value stream :start start :end index)
            (case code
              (34 (write-string "\\\"" stream))
              (92 (write-string "\\\\" stream))
              (10 (write-string "\\n" stream))
              (13 (write-string "\\r" stream))
              (9 (write-string "\\t" stream))
              (otherwise
               (if (< code #x10000)
                   (unicode-escape code)
                   (let ((offset (- code #x10000)))
                     (unicode-escape (+ #xD800 (ash offset -10)))
                     (unicode-escape (+ #xDC00 (logand offset #x3FF)))))))
            (setf start (1+ index))
          finally (write-string value stream :start start))
    (write-char #\" stream)))
