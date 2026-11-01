{ pkgs, pythonPackages, mkHermesPlugin }:
let
  python = pythonPackages.python;
  sealed = python.withPackages (ps: [ ps.agent-client-protocol ps.packaging ]);
  plugin = mkHermesPlugin { hermes = { inherit python; hermesVenv = sealed; }; };
  missingSDK = python.withPackages (ps: [ ps.packaging ]);
in pkgs.runCommand "hermes-omp-acp-sealed-${python.pythonVersion}" { } ''
  cd "$TMPDIR"
  export PYTHONPATH="${plugin}/${python.sitePackages}"
  ${sealed}/bin/python ${./check-installed.py}
  ${sealed}/bin/python ${./check-runtime-requirements.py}

  # A missing dependency must not pass just because the builder had the SDK.
  if ${missingSDK}/bin/python ${./check-runtime-requirements.py} > missing.log 2>&1; then
    echo "Missing sealed SDK was accepted" >&2
    exit 1
  fi
  grep -F 'installed missing' missing.log

  # Independently supplied incompatible metadata must fail before SDK imports.
  mkdir -p incompatible/agent_client_protocol-0.8.0.dist-info
  printf 'Metadata-Version: 2.1\nName: agent-client-protocol\nVersion: 0.8.0\n' \
    > incompatible/agent_client_protocol-0.8.0.dist-info/METADATA
  if PYTHONPATH="$TMPDIR/incompatible:$PYTHONPATH" \
    ${sealed}/bin/python ${./check-runtime-requirements.py} > incompatible.log 2>&1; then
    echo "Incompatible sealed SDK was accepted" >&2
    exit 1
  fi
  grep -F 'installed 0.8.0' incompatible.log
  touch "$out"
''
