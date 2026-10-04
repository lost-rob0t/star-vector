:- module(star_vector_architecture,
          [ starintel_schema_authority/4,
            storage_contract/3,
            search_metric/3,
            foreign_interface/3
          ]).

starintel_schema_authority('0.10.1',
                           'nsaspy/star-lang',
                           '919833266723edc9bddb337d606f72ae25fb8ced',
                           'schema/starintel-schema.lock.json').

storage_contract('star-vector/1', atomic_json_snapshot, single_writer_process).

search_metric(cosine, similarity, descending).
search_metric(dot, similarity, descending).
search_metric(euclidean, distance, ascending).
search_metric(manhattan, distance, ascending).

foreign_interface(c, 'include/star_vector.h', owned_result_strings).
foreign_interface(common_lisp, 'lisp/star-vector.asd', cffi_grovel).
