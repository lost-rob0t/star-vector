{ pkgs, source, lispPackages, lispRegistry, profileScript ? null }:
let
  loadSystem = pkgs.lib.escapeShellArgs [
    "--eval" "(require :asdf)"
    "--eval" "(asdf:load-system :cffi)"
    "--eval" ''(push #P"${pkgs.lmdb.out}/lib/" cffi:*foreign-library-directories*)''
    "--eval" ''(asdf:load-asd #P"${source}/lisp/star-vector-tek9.asd")''
    "--eval" ''(asdf:load-system "star-vector-tek9")''
  ];
in pkgs.runCommand "star-vector-tek9-worker" {
  nativeBuildInputs = [ pkgs.sbcl pkgs.stdenv.cc ] ++ lispPackages;
} ''
  export XDG_CACHE_HOME="$out/lib/cache"
  export CL_SOURCE_REGISTRY="${lispRegistry}:"
  mkdir -p "$out/bin"
  # Compile once during packaging, preserving CFFI's generated shared objects.
  sbcl --noinform --non-interactive --no-userinit --no-sysinit ${loadSystem}
  ${pkgs.lib.optionalString (profileScript != null) ''
    sbcl --noinform --non-interactive --no-userinit --no-sysinit ${loadSystem} \
      --eval "(compile-file \"${profileScript}\" :output-file \"$out/lib/profile.fasl\")"
  ''}
  cat > "$out/bin/star-vector-tek9-worker" <<EOF
  #!${pkgs.runtimeShell}
  export XDG_CACHE_HOME="$out/lib/cache"
  export CL_SOURCE_REGISTRY="${lispRegistry}:"
  exec ${pkgs.sbcl}/bin/sbcl --noinform --non-interactive --no-userinit --no-sysinit \
    ${loadSystem} \
    ${if profileScript == null then "" else "--load \"$out/lib/profile.fasl\""} \
    --eval '(${if profileScript == null then "star-vector-tek9:run" else "sv-memory-profile:run"})'
  EOF
  chmod +x "$out/bin/star-vector-tek9-worker"
''
