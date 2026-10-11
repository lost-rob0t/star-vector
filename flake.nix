{
  description = "Exact Nim vector storage for StarIntel documents with C and Common Lisp clients";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  inputs.tek9.url = "git+https://github.com/lost-rob0t/tek9?rev=e6d1cb86a2e268a894eae3acae9617238b2adcff";
  inputs.tek9.inputs.nixpkgs.follows = "nixpkgs";

  outputs =
    { self, nixpkgs, tek9 }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          producer = tek9.packages.${system}.tek9;
          lispPackages = pkgs.lib.closePropagation [
            producer
            pkgs.sbcl.pkgs.cffi
            pkgs.sbcl.pkgs.cffi-grovel
          ];
          lispRegistry = pkgs.lib.concatMapStringsSep ":" (package: "${package}//") lispPackages;
          worker = pkgs.runCommand "star-vector-tek9-worker" {
            nativeBuildInputs = [ pkgs.sbcl pkgs.stdenv.cc ] ++ lispPackages;
          } ''
            export XDG_CACHE_HOME="$out/lib/cache"
            export CL_SOURCE_REGISTRY="${lispRegistry}:"
            mkdir -p "$out/bin"
            sbcl --noinform --non-interactive --no-userinit --no-sysinit \
              --eval '(require :asdf)' \
              --eval '(asdf:load-system :cffi)' \
              --eval '(push #P"${pkgs.lmdb.out}/lib/" cffi:*foreign-library-directories*)' \
              --eval '(asdf:load-asd #P"${self}/lisp/star-vector-tek9.asd")'  \
              --eval '(asdf:load-system "star-vector-tek9")' \
              --eval "(sb-ext:save-lisp-and-die \"$out/bin/star-vector-tek9-worker\" :toplevel #'star-vector-tek9:run :executable t)"
          '';
          package = pkgs.stdenv.mkDerivation {
            pname = "star-vector";
            version = "0.1.0";
            src = self;

            nativeBuildInputs = [
              pkgs.nim
              pkgs.python3
              pkgs.sbcl
              worker
            ]
            ++ lispPackages;

            strictDeps = true;
            dontConfigure = true;
            doCheck = true;
            doInstallCheck = true;

            buildPhase = ''
              runHook preBuild
              export HOME="$TMPDIR/home"
              mkdir -p "$HOME"
              export STAR_VECTOR_TEK9_WORKER="${worker}/bin/star-vector-tek9-worker"
              mkdir -p build
              nim c -d:release --threads:on --mm:orc --path:src \
                -d:tek9Worker=${worker}/bin/star-vector-tek9-worker --out:build/star-vector src/star_vector_cli.nim
              nim c -d:release -d:noSignalHandler --app:lib --threads:on --mm:orc --path:src \
                -d:tek9Worker=${worker}/bin/star-vector-tek9-worker --out:build/libstar_vector.so src/star_vector/c_api.nim
              runHook postBuild
            '';

            checkPhase = ''
              runHook preCheck
              export STAR_VECTOR_TEK9_WORKER="${worker}/bin/star-vector-tek9-worker"
              export HOME="$TMPDIR/home"
              export XDG_CACHE_HOME="$TMPDIR/cache"
              export CL_SOURCE_REGISTRY="${lispRegistry}:"
              mkdir -p "$HOME" "$XDG_CACHE_HOME"

              python3 -m py_compile scripts/*.py
              python3 -m unittest discover -s tests -p 'test_*.py' -v
              python3 scripts/sync.py
              python3 scripts/sync.py --check
              python3 scripts/validate-docs.py
              python3 scripts/sync-starintel-schema.py --check

              nim c -r --threads:on --mm:orc --path:src \
                --out:build/test-vector-store tests/test_vector_store.nim
              nim c -r --threads:on --mm:orc --path:src \
                --out:build/test-search-reference tests/test_search_reference.nim
              $CC -std=c11 -Wall -Wextra -Werror -Iinclude tests/test_c_api.c \
                -Lbuild -lstar_vector -Wl,-rpath,"$PWD/build" \
                -o build/test-c-api
              build/test-c-api
              python3 tests/storage_integration.py "$PWD/build/star-vector" "$PWD/build/libstar_vector.so"
              STAR_VECTOR_CLI="$PWD/build/star-vector" bash tests/test_cli.sh

              STAR_VECTOR_LIBRARY="$PWD/build/libstar_vector.so" \
                C_INCLUDE_PATH="$PWD/include''${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}" \
                sbcl --noinform --non-interactive --no-userinit --no-sysinit \
                  --eval '(require :asdf)' \
                  --eval '(asdf:load-asd (truename "lisp/star-vector.asd"))' \
                  --eval '(asdf:test-system "star-vector/tests")'
              python3 benchmarks/storage_benchmark.py "$PWD/build/libstar_vector.so" \
                1000,10000,50000 build/storage-benchmark.json
              runHook postCheck
            '';

            installPhase = ''
              runHook preInstall
              install -Dm755 build/star-vector "$out/bin/star-vector"
              ln -s "${worker}/bin/star-vector-tek9-worker" "$out/bin/star-vector-tek9-worker"
              install -Dm755 build/libstar_vector.so "$out/lib/libstar_vector.so"
              install -Dm644 build/storage-benchmark.json "$out/share/star-vector/storage-benchmark.json"
              install -Dm644 include/star_vector.h "$out/include/star_vector.h"

              nimRoot="$out/share/nimble/star_vector"
              mkdir -p "$nimRoot/src" "$nimRoot/schema"
              cp -R src/. "$nimRoot/src/"
              cp star_vector.nimble "$nimRoot/"
              cp schema/starintel-*.json "$nimRoot/schema/"

              lispRoot="$out/share/common-lisp/source/star-vector"
              mkdir -p "$lispRoot"
              cp lisp/*.lisp lisp/*.asd "$lispRoot/"
              runHook postInstall
            '';

            installCheckPhase = ''
              runHook preInstallCheck
              sourceRoot="$PWD"
              mkdir -p "$TMPDIR/installed-check"
              cd "$TMPDIR/installed-check"
              export XDG_CACHE_HOME="$TMPDIR/installed-cache"
              export CL_SOURCE_REGISTRY="${lispRegistry}:"
              STAR_VECTOR_CLI="$out/bin/star-vector" bash "$sourceRoot/tests/test_cli.sh"
              $CC -std=c11 -Wall -Wextra -Werror -I"$out/include" \
                "$sourceRoot/tests/test_c_api.c" -L"$out/lib" -lstar_vector \
                -Wl,-rpath,"$out/lib" -o test-installed-c-api
              ./test-installed-c-api
              STAR_VECTOR_LIBRARY="$out/lib/libstar_vector.so" \
                C_INCLUDE_PATH="$out/include" \
                sbcl --noinform --non-interactive --no-userinit --no-sysinit \
                  --eval '(require :asdf)' \
                  --eval "(asdf:load-asd (truename \"$out/share/common-lisp/source/star-vector/star-vector.asd\"))" \
                  --eval '(asdf:test-system "star-vector/tests")'
              cd "$sourceRoot"
              runHook postInstallCheck
            '';

            meta = {
              description = "Exact vector storage and search for StarIntel documents";
              homepage = "https://github.com/lost-rob0t/star-vector";
              license = pkgs.lib.licenses.agpl3Only;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "star-vector";
            };
          };
        in
        {
          default = package;
          star-vector = package;
          tek9-worker = worker;
        }
      );

      checks = forAllSystems (system: {
        default = self.packages.${system}.default;
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/star-vector";
          meta.description = "Star Vector command-line client";
        };
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          producer = tek9.packages.${system}.tek9;
          lispPackages = pkgs.lib.closePropagation [
            producer
            pkgs.sbcl.pkgs.cffi
            pkgs.sbcl.pkgs.cffi-grovel
          ];
          lispRegistry = pkgs.lib.concatMapStringsSep ":" (package: "${package}//") lispPackages;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.git
              pkgs.gnumake
              pkgs.nim
              pkgs.pkg-config
              pkgs.python3
              pkgs.sbcl
              pkgs.swi-prolog
            ]
            ++ lispPackages
            ++ pkgs.lib.optional (pkgs ? nimble) pkgs.nimble;
            shellHook = ''
              export CL_SOURCE_REGISTRY="${lispRegistry}:"
              export STAR_VECTOR_TEK9_WORKER="${self.packages.${system}.tek9-worker}/bin/star-vector-tek9-worker"
            '';
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
