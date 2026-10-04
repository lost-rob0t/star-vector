{
  description = "Exact Nim vector storage for StarIntel documents with C and Common Lisp clients";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
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
          lispPackages = pkgs.lib.closePropagation [
            pkgs.sbcl.pkgs.cffi
            pkgs.sbcl.pkgs.cffi-grovel
          ];
          lispRegistry = pkgs.lib.concatMapStringsSep ":" (package: "${package}//") lispPackages;
          package = pkgs.stdenv.mkDerivation {
            pname = "star-vector";
            version = "0.1.0";
            src = self;

            nativeBuildInputs = [
              pkgs.nim
              pkgs.python3
              pkgs.sbcl
            ]
            ++ lispPackages;

            strictDeps = true;
            dontConfigure = true;
            doCheck = true;

            buildPhase = ''
              runHook preBuild
              export HOME="$TMPDIR/home"
              mkdir -p "$HOME"
              mkdir -p build
              nim c -d:release --threads:on --mm:orc --path:src \
                --out:build/star-vector src/star_vector_cli.nim
              nim c -d:release --app:lib --threads:on --mm:orc --path:src \
                --out:build/libstar_vector.so src/star_vector/c_api.nim
              runHook postBuild
            '';

            checkPhase = ''
              runHook preCheck
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
              $CC -std=c11 -Wall -Wextra -Werror -Iinclude tests/test_c_api.c \
                -Lbuild -lstar_vector -Wl,-rpath,"$PWD/build" \
                -o build/test-c-api
              build/test-c-api
              STAR_VECTOR_CLI="$PWD/build/star-vector" bash tests/test_cli.sh

              STAR_VECTOR_LIBRARY="$PWD/build/libstar_vector.so" \
                C_INCLUDE_PATH="$PWD/include''${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}" \
                sbcl --noinform --non-interactive --no-userinit --no-sysinit \
                  --eval '(require :asdf)' \
                  --eval '(asdf:load-asd (truename "lisp/star-vector.asd"))' \
                  --eval '(asdf:test-system "star-vector/tests")'
              runHook postCheck
            '';

            installPhase = ''
              runHook preInstall
              install -Dm755 build/star-vector "$out/bin/star-vector"
              install -Dm755 build/libstar_vector.so "$out/lib/libstar_vector.so"
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
          lispPackages = pkgs.lib.closePropagation [
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
            '';
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
