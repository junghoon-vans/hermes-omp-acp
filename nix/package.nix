{ lib, buildPythonPackage, pythonOlder, hatchling, pyyaml, agent-client-protocol
, hermesRuntime ? null
}:
let
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../hatch_build.py
      ../plugin.yaml
      ../README.md
      ../LICENSE
      (lib.fileset.difference ../hermes_omp_acp (
        lib.fileset.fileFilter (file: file.hasExt "pyc" || file.hasExt "pyo") ../hermes_omp_acp
      ))
      ../skills
    ];
  };
  # plugin.yaml remains the version source of truth, as for the wheel build.
  version = builtins.head (builtins.match ".*\nversion: ([^\n]+)\n.*" (builtins.readFile ../plugin.yaml));
in buildPythonPackage {
  pname = "hermes-omp-acp";
  inherit version;
  pyproject = true;
  src = source;
  build-system = [ hatchling pyyaml ];
  disabled = pythonOlder "3.11";
  # A sealed Hermes host already owns the SDK. Keep the normal wheel requirement
  # and build-time checks, but never propagate a second SDK into its PYTHONPATH.
  dependencies = lib.optional (hermesRuntime == null) agent-client-protocol;
  nativeBuildInputs = lib.optional (hermesRuntime != null) agent-client-protocol;
  postInstall = lib.optionalString (hermesRuntime != null) ''
    (
      cd "$TMPDIR"
      PYTHONPATH="$out/${hermesRuntime.python.sitePackages}" \
        ${hermesRuntime.hermesVenv}/bin/python ${./check-runtime-requirements.py}
    )
  '';
  # Nix's Python interpreter prefix is not this distribution's output prefix.
  # Keep resources tied to the package, including in a composed withPackages env.
  postPatch = ''
    substituteInPlace hermes_omp_acp/skill_resources.py \
      --replace-fail 'Path(sys.prefix)' 'Path("'"$out"'")'
  '';
  pythonImportsCheck = [ "hermes_omp_acp" "hermes_omp_acp.skill_resources" ];
  # Provider runtime tests require a separately packaged Hermes; CI owns those.
  doCheck = false;
  meta = {
    description = "Use Oh My Pi as Hermes Agent's model backend through ACP";
    homepage = "https://github.com/junghoon-vans/hermes-omp-acp";
    license = lib.licenses.mit;
  };
}
