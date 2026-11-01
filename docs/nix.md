# Nix installation

The flake packages the provider, not Hermes or OMP. Its locked nixpkgs input
and fixed-output ACP SDK source make dependency resolution reproducible.
The SDK is exactly 0.9.0, matching the supported official Hermes `acp` extra;
no Hermes requirement is patched or relaxed.

## Build and check

From a checkout with Nix flakes enabled:

```sh
nix build
nix flake check
./result/bin/hermes-omp-acp-skills
```

The default package uses Python 3.13, suitable for Hermes 0.21.4, and includes
both plugin entry points, the `hermes-omp-acp-skills` command, packaged OMP ACP
usage guidance, and the external `omp-web-search` skill. Checks inspect the
installed distribution outside the source tree and exercise the command in
composed Python 3.13 and 3.14 environments. Sealed-host checks additionally
reject missing and incompatible SDKs. Flake checks do not launch Hermes or
call a model; `nix/check-hermes.py` checks discovery and skill loading when
run with a composed Hermes runtime.

Outputs cover x86_64/aarch64 Linux and Darwin. CI builds on Ubuntu and macOS;
other architectures need their own builders. Update dependencies explicitly
with `nix flake update`, then repeat both build and checks.

## Stable releases and main

CI keeps PR validation and `main` push builds, and also builds pushed release
tags (`vX.Y.Z`, optionally followed by `aN`, `bN`, or `rcN`) on Ubuntu and macOS.
Tag builds validate the checked-out tag against that checkout's `plugin.yaml`,
Python package identity, and evaluated Nix package version before building.
They do not fetch `main` to validate a release.

For stable use, select a published release tag that **contains `flake.nix`**;
older releases such as `v0.2.3` do not contain the flake. The following values
are placeholders, not a claim that a Nix-enabled release already exists.
Replace `X.Y.Z` with the same published version for Python and Nix:

```sh
# Only for a user-managed Hermes Python environment:
python -m pip install 'hermes-omp-acp==X.Y.Z'

# Build the provider from the matching release source:
nix build 'github:junghoon-vans/hermes-omp-acp/vX.Y.Z'
```

For Nix-managed Hermes, use the matching input and the composition below instead
of pip:

```nix
inputs.hermes-omp-acp.url = "github:junghoon-vans/hermes-omp-acp/vX.Y.Z";
```

For development, replace `vX.Y.Z` with `main` and commit your configuration's
`flake.lock` to pin its resolved commit. Python publication still starts on a
published GitHub Release; a tag push starts Nix validation without publishing
Python packages. These workflows are independent: neither waits for the other's
success. Nix builds do not publish a binary cache; consumers build locally or
use their existing substituters.

## Compose with Hermes

Adding the provider as a separate `environment.systemPackages` or Home Manager
package does **not** expose its Python imports or entry points to Hermes.
Use the same Python package set and interpreter that builds/runs Hermes.

Add this flake as an input named `hermes-omp-acp` to your configuration and pin
it with your configuration's lockfile. You may set
`inputs.hermes-omp-acp.inputs.nixpkgs.follows = "nixpkgs"`; that selects your
nixpkgs revision instead of this repository's lock, so rerun the checks there.

### Sealed Hermes packages (0.22.x)

For Hermes packages exposing `python`, `hermesVenv`, and
`override { extraPythonPackages = ...; }`, use the host-aware factory:

```nix
let
  hermes = inputs.hermes-agent.packages.${system}.messaging.override {
    extraDependencyGroups = [ "messaging" "acp" ];
  };
  provider = inputs.hermes-omp-acp.lib.mkHermesPlugin { inherit hermes; };
in hermes.override {
  extraPythonPackages = [ provider ];
}
```

The factory builds the provider for `hermes.python` and checks the wheel's
unchanged runtime requirements against **that host's sealed `hermesVenv`**.
The sealed runtime must already supply a compatible ACP SDK (0.9.0 on the
supported Hermes baseline). Missing or incompatible dependencies fail the
provider build. The SDK remains available for normal build-time dependency
and import checks, but is not propagated into the host's `PYTHONPATH`.
Hermes's duplicate-package guard remains enabled.

Use the same base `hermes` for the factory and the final override, including
any `extraDependencyGroups` override. A provider validated against one host
is not validated for a different host. The standalone packages and Python
overlays below still include their SDK; do not pass them directly to a
sealed host or strip dependencies in the consumer.

The factory does not enable plugins or configure credentials. Point external
skill configuration at `"${provider}/share/hermes-omp-acp/skills"` if needed.
For installed verification, run `nix/check-hermes.py` with
`hermes.hermesVenv/bin/python` and
`PYTHONPATH="${provider}/${hermes.python.sitePackages}"`. The script creates a
temporary profile before importing Hermes, discovers the installed entry points,
and explicitly loads both skill bodies outside the source tree.

### User-composed Python environments

For a Python 3.14 Hermes package, apply the overlay to its nixpkgs instance:

```nix
let
  pkgs = import inputs.nixpkgs {
    inherit system;
    overlays = [ inputs.hermes-omp-acp.overlays.default ];
  };
  python = pkgs.python314;
  # Build your Hermes derivation with this package set as well.
  pythonPackages = python.pkgs;
  runtime = python.withPackages (ps: [
    ps.hermes-omp-acp
    # Include your existing Hermes Python package and its required extras here.
  ]);
in runtime
```

The snippet supplies the provider environment; it does not define a Hermes
package. Add your existing Hermes derivation to `withPackages`, using the same
`pythonPackages` for its dependencies. If your Hermes package wraps its own
interpreter, rebuild that wrapper to use this runtime; merely placing two
executables on PATH is insufficient. Use Python 3.11–3.13 for Hermes 0.21.4
or Python 3.14 for Hermes 0.22.x.

For an existing customized Python interpreter, compose the Python-scope overlay
instead of replacing its overrides:

```nix
python = existingPython.override (old: {
  packageOverrides = pkgs.lib.composeExtensions
    (old.packageOverrides or (_: _: {}))
    inputs.hermes-omp-acp.overlays.python;
});
```

The overlay selects ACP SDK 0.9.0 for the entire chosen package set. Check that
other consumers in that set support it; do not force a conflicting Hermes SDK
requirement. Both overlays expose `python.pkgs.hermes-omp-acp` and
`python.pkgs.agent-client-protocol`.

After rebuilding, run these with the **Hermes runtime's Python**, not an
unrelated `python` on PATH:

```sh
python -c 'from importlib.metadata import version; print(version("hermes-omp-acp"), version("agent-client-protocol"))'
python -c 'from importlib.metadata import entry_points; print(list(entry_points(group="hermes_agent.plugins")))'
hermes-omp-acp-skills
hermes plugins enable omp-acp-provider
```

Select the intended `HERMES_HOME` before enabling plugins, preserve existing
configuration, and restart Hermes/gateway after rebuilding. Then follow the
[model configuration](../README.md#quick-start) and [optional skill
configuration](configuration.md#optional-bundled-omp-acp-usage-skill).
OMP must separately be installed, authenticated, and available on the Hermes
process's PATH. Nix does not configure credentials, profiles or model routes;
never run pip to mutate this managed environment.
