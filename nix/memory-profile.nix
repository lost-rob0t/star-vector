{ pkgs, baseline, candidate, validatedPackage, lispPackages, lispRegistry }:
let
  variant = name: source: pkgs.stdenv.mkDerivation {
    pname = "star-vector-memory-${name}";
    version = "1";
    src = source;
    nativeBuildInputs = [ pkgs.nim pkgs.sbcl ] ++ lispPackages;
    dontConfigure = true;
    buildPhase = ''
      export HOME="$TMPDIR/home"
      export XDG_CACHE_HOME="$out/lib/cache"
      export CL_SOURCE_REGISTRY="${lispRegistry}:"
      mkdir -p "$HOME" "$out/bin" "$out/lib"
      nim c -d:release -d:noSignalHandler -d:useMalloc --app:lib --threads:on --mm:orc \
        --path:src --out:"$out/lib/libstar_vector.so" src/star_vector/c_api.nim
      sbcl --noinform --non-interactive --no-userinit --no-sysinit \
        --eval '(require :asdf)' --eval '(require :sb-posix)' \
        --eval '(asdf:load-system :cffi)' \
        --eval '(push #P"${pkgs.lmdb.out}/lib/" cffi:*foreign-library-directories*)' \
        --eval '(asdf:load-asd (truename "lisp/star-vector-tek9.asd"))' \
        --eval '(asdf:load-system "star-vector-tek9")' \
        --load ${candidate}/benchmarks/worker-profile.lisp \
        --eval "(sb-ext:save-lisp-and-die \"$out/bin/star-vector-tek9-worker\" :toplevel #'sv-memory-profile:run :executable t)"
    '';
    installPhase = "true";
  };
  before = variant "baseline" baseline;
  after = variant "candidate" candidate;
in pkgs.runCommand "star-vector-memory-profile" {
  nativeBuildInputs = [ pkgs.python3 ];
  buildInputs = [ validatedPackage ]; # Finish ordinary CI workloads before measuring.
} ''
  mkdir -p "$out"
  python3 ${candidate}/benchmarks/memory_profile.py \
    ${before} ${after} "$out/memory-profile.json"
''
