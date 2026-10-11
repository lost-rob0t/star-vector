{ pkgs, source, vectorPackage, lispPackages, lispRegistry }:
let
  pythonClient = pkgs.python3Packages.buildPythonPackage {
    pname = "star-vector-client";
    version = "0.1.0";
    pyproject = true;
    src = source + /clients/python;
    build-system = [ pkgs.python3Packages.setuptools ];
    pythonImportsCheck = [ "star_vector_client" ];
  };
  python = pkgs.python3.withPackages (ps: [ pythonClient ps.spacy ps.numpy ]);
  wheel = pkgs.fetchurl {
    url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl";
    hash = "sha256-XmMp/j/s7bHRoCw+ohcu4P7ebOpuSu+2oC2DLbp4oxA=";
  };
  model = pkgs.runCommand "star-vector-en-core-web-md-3.8.0" { nativeBuildInputs = [ pkgs.unzip ]; } ''
    unzip -q ${wheel}
    mkdir -p "$out"
    cp -R en_core_web_md/en_core_web_md-3.8.0/. "$out/"
    cp en_core_web_md-3.8.0.dist-info/LICENSE "$out/MODEL_LICENSE"
  '';
  textPackage = pkgs.symlinkJoin {
    name = "star-vector-text-0.1.0";
    paths = [ vectorPackage ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      makeWrapper ${python}/bin/python "$out/bin/star-vector-text" \
        --add-flags '-m star_vector_client' \
        --set STAR_VECTOR_MODEL ${model} \
        --set-default STAR_VECTOR_LIBRARY ${vectorPackage}/lib/libstar_vector.so
      rm "$out/bin/star-vector"
      makeWrapper ${vectorPackage}/bin/star-vector "$out/bin/star-vector" \
        --set STAR_VECTOR_TEXT_HELPER "$out/bin/star-vector-text"
    '';
  };
  check = pkgs.runCommand "star-vector-text-check" {
    nativeBuildInputs = [ python pkgs.nim pkgs.stdenv.cc pkgs.nodejs pkgs.typescript pkgs.sbcl textPackage ] ++ lispPackages;
  } ''
    export HOME="$TMPDIR/home" XDG_CACHE_HOME="$TMPDIR/cache"
    mkdir -p "$HOME" "$XDG_CACHE_HOME"
    cp -R ${source} source
    chmod -R u+w source
    cd source
    export STAR_VECTOR_LIBRARY=${vectorPackage}/lib/libstar_vector.so
    export STAR_VECTOR_TEK9_WORKER=${vectorPackage}/bin/star-vector-tek9-worker
    export STAR_VECTOR_MODEL=${model}
    export STAR_VECTOR_CLI=${textPackage}/bin/star-vector
    export STAR_VECTOR_TEXT_HELPER=${textPackage}/bin/star-vector-text
    export PYTHONPATH="$PWD/clients/python"
    export CL_SOURCE_REGISTRY="${lispRegistry}:"
    export C_INCLUDE_PATH="$PWD/include"
    python tests/embedding_integration.py
    tsc --noEmit --strict --module NodeNext --moduleResolution NodeNext --target ES2022 tests/text_client_types.mts
    nim c --threads:on --mm:orc --path:src --out:test-text-client tests/test_text_client.nim
    python tests/text_clients.py --nim-client "$PWD/test-text-client" --output evidence.json
    mkdir -p "$out"
    cp evidence.json "$out/"
  '';
in
{ inherit pythonClient model textPackage check; }
